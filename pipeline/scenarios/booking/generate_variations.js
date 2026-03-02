const fs = require('fs');
const fse = require('fs-extra');
const path = require('path');
const { JSDOM } = require('jsdom');

// Unified variation config - all variant types
const UNIFIED_VARIATIONS = {
  // Position variants (header/banner/spotlight for first hotel, sidebar from the legacy position-only scenario)
  positions: [
    { name: 'header', description: 'Header at top of page' },
    { name: 'banner', description: 'Banner at top of page' },
    { name: 'spotlight', description: 'Spotlight in middle of search results' },
    { name: 'sidebar', description: 'Sidebar position, card scaled to fit sidebar width' }
  ],
  
  // Background color variants (12)
  backgroundColors: [
    { name: '2196f3', value: '#2196f3' },
    { name: '1976d2', value: '#1976d2' }, // darker blue, similar to 2196f3
    { name: '42a5f5', value: '#42a5f5' }, // lighter blue, similar to 2196f3
    { name: '4caf50', value: '#4caf50' },
    { name: 'e91e63', value: '#e91e63' },
    { name: 'ffeb3b', value: '#ffeb3b' },
    { name: '6f42c1', value: '#6f42c1' },
    { name: 'ff9800', value: '#ff9800' },
    { name: '00bcd4', value: '#00bcd4' },
    { name: 'f44336', value: '#f44336' }, // red
    { name: '9c27b0', value: '#9c27b0' }, // purple
    { name: '000000', value: '#000000' }  // black - edge case
  ],
  
  // Text color variants (6)
  textColors: [
    { name: '111111', value: '#111111' },
    { name: '0d6efd', value: '#0d6efd' },
    { name: 'dc3545', value: '#dc3545' },
    { name: '198754', value: '#198754' },
    { name: '6f42c1', value: '#6f42c1' },
    { name: 'ffffff', value: '#ffffff' }  // white - edge case
  ],
  
  // Font variants (13)
  fontFamilies: [
    { name: 'arial', value: 'Arial, sans-serif' },
    { name: 'helvetica', value: 'Helvetica, sans-serif' },
    { name: 'courier', value: 'Courier New, monospace' },
    { name: 'georgia', value: 'Georgia, serif' },
    { name: 'times', value: 'Times New Roman, serif' },
    { name: 'verdana', value: 'Verdana, sans-serif' },
    { name: 'lucida', value: 'Lucida Console, monospace' },
    { name: 'comic', value: 'Comic Sans MS, cursive' },
    { name: 'inter', value: 'Inter, sans-serif' },
    { name: 'roboto', value: 'Roboto, sans-serif' },
    { name: 'opensans', value: 'Open Sans, sans-serif' },
    { name: 'merriweather', value: 'Merriweather, serif' },
    { name: 'jetbrains-mono', value: '"JetBrains Mono", monospace' }
  ],
  
  // Font size variants (5)
  fontSizes: ['14px', '16px', '18px', '20px', '24px'],
  
  // Image clarity variants (6)
  imageClarity: [
    { name: 'sharp', filter: 'none' },
    { name: 'very_sharp', filter: 'contrast(1.2) saturate(1.1)' },
    { name: 'blur_1px', filter: 'blur(1px)' },
    { name: 'blur_2px', filter: 'blur(2px)' },
    { name: 'blur_4px', filter: 'blur(4px)' },
    { name: 'blur_8px', filter: 'blur(8px)' }
  ],
  
  // Card clarity variants (4)
  cardClarity: [
    { name: 'sharp', filter: 'none' },
    { name: 'blur_1px', filter: 'blur(1px)' },
    { name: 'blur_2px', filter: 'blur(2px)' },
    { name: 'blur_4px', filter: 'blur(4px)' }
  ],
  
  // Card size variants (4)
  cardSizes: [
    { name: 'scale_0.6', scale: 0.6 },
    { name: 'scale_0.8', scale: 0.8 },
    { name: 'scale_1.0', scale: 1.0 },
    { name: 'scale_1.2', scale: 1.2 }
    // { name: 'scale_1.5', scale: 1.5 }  // commented out
  ]
};

function loadConfig(customPath) {
  const cfgPath = customPath || path.resolve(__dirname, 'config_booking.json');
  const raw = fs.readFileSync(cfgPath, 'utf-8');
  return JSON.parse(raw);
}

// Seeded random number generator for deterministic results
class SeededRandom {
  constructor(seed = 12345) {
    this.seed = seed;
  }
  
  // Linear congruential generator
  next() {
    this.seed = (this.seed * 1664525 + 1013904223) % Math.pow(2, 32);
    return this.seed / Math.pow(2, 32);
  }
  
  // Generate random integer between min and max (inclusive)
  nextInt(min, max) {
    return Math.floor(this.next() * (max - min + 1)) + min;
  }
  
  // Generate random float between min and max
  nextFloat(min, max) {
    return this.next() * (max - min) + min;
  }
}

// Global seeded random instance
const seededRandom = new SeededRandom(12345);

function readSnapshot(snapshotPath) {
  const html = fs.readFileSync(snapshotPath, 'utf-8');
  return html; // no script stripping, keep original structure
}

function ensureBaseHref(dom, cfg) {
  if (!cfg.addBaseTag) return;
  const doc = dom.window.document;
  let base = doc.querySelector('base');
  if (!base) {
    base = doc.createElement('base');
    doc.head.appendChild(base);
  }
  base.setAttribute('href', cfg.baseHref || 'https://www.booking.com/');
}

function removeNodes(doc, selectors = []) {
  selectors.forEach(sel => {
    doc.querySelectorAll(sel).forEach(n => n.remove());
  });
}

function findHotelCard(doc, cfg) {
  console.log('Searching for hotel cards...');
  
  // Try multiple selectors to find hotel cards
  const selectors = [
    '[data-testid*="property-card"]',
    '[data-testid*="sr-card"]',
    '[class*="sr_property"]',
    '[class*="property"]',
    '[class*="hotel"]',
    '[class*="card"]',
    '[class*="item"]',
    '[class*="listing"]'
  ];
  
  let targetCard = null;
  let cardIndex = -1;
  
  // Hardcoded: look for specific Holiday Inn San Francisco - Golden Gateway
  console.log('Looking for Holiday Inn San Francisco - Golden Gateway...');
  
  for (const selector of selectors) {
    const cards = doc.querySelectorAll(selector);
    console.log(`Trying selector ${selector}: found ${cards.length} elements`);
    
    for (let i = 0; i < cards.length; i++) {
      const card = cards[i];
      const text = card.textContent || '';
      
      // Check if it contains key text for Holiday Inn San Francisco - Golden Gateway
      if (text.includes('Holiday Inn San Francisco') && 
          text.includes('Golden Gateway') && 
          (text.includes('Nob Hill') || text.includes('0.8 miles from downtown') || 
           text.includes('8.3') || text.includes('Very Good') || text.includes('4,208'))) {
        targetCard = card;
        cardIndex = i;
        console.log(`Found Holiday Inn San Francisco - Golden Gateway card (selector: ${selector}, index: ${i})`);
        console.log(`  Card text snippet: ${text.substring(0, 200)}...`);
        break;
      }
    }
    
    if (targetCard) break;
  }
  
  // If Holiday Inn not found, try by location features
  if (!targetCard) {
    console.log('Holiday Inn not found, trying location features...');
    for (const selector of selectors) {
      const cards = doc.querySelectorAll(selector);
      
      for (let i = 0; i < cards.length; i++) {
        const card = cards[i];
        const text = card.textContent || '';
        
        // Check for "Nob Hill" and "0.8 miles from downtown"
        if (text.includes('Nob Hill') && 
            text.includes('0.8 miles from downtown') &&
            text.includes('San Francisco')) {
          targetCard = card;
          cardIndex = i;
          console.log(`Found target card by location features (selector: ${selector}, index: ${i})`);
          console.log(`  Card text snippet: ${text.substring(0, 200)}...`);
          break;
        }
      }
      
      if (targetCard) break;
    }
  }
  
  // If still not found, use fallback logic
  if (!targetCard) {
    console.log('Using fallback logic to find hotel card...');
    
    for (const selector of selectors) {
      const cards = doc.querySelectorAll(selector);
      console.log(`Trying selector ${selector}: found ${cards.length} elements`);
      
      if (cards.length > 0) {
        // Collect all valid hotel cards
        const validCards = [];
        for (let i = 0; i < cards.length; i++) {
          const card = cards[i];
          const text = card.textContent || '';
          
          // Find cards with hotel-related info - stricter conditions
          if (text.length > 200 && text.length < 5000 && 
              (text.includes('Hotel') || text.includes('hotel') || 
               text.includes('$') || text.includes('per night') ||
               text.includes('stars') || text.includes('rating') ||
               text.includes('Show prices') || text.includes('Excellent') ||
               text.includes('miles from') || text.includes('from center'))) {
            
            // Ensure this is a full hotel card, not a title element
            // Check for typical hotel card elements
            const hasImage = card.querySelector('img') || card.querySelector('[class*="image"]');
            const hasPrice = text.includes('$') || text.includes('price');
            const hasRating = text.includes('Excellent') || text.includes('stars') || text.includes('rating') || text.includes('Good') || text.includes('Very good');
            const hasLocation = text.includes('miles from') || text.includes('from center') || text.includes('San Francisco') || text.includes('California');
            
            if (hasImage || (hasPrice && hasRating && hasLocation)) {
              validCards.push({ card, index: i });
            }
          }
        }
        
        if (validCards.length > 0) {
          // Select hotel card in middle position, fixed seed for consistency
          let selectedIndex;
          if (validCards.length > 4) {
            // If more than 4 cards, choose from middle 50%
            const startIndex = Math.floor(validCards.length * 0.25); // start at 25%
            const endIndex = Math.floor(validCards.length * 0.75);   // end at 75%
            const middleRange = endIndex - startIndex;
            selectedIndex = startIndex + seededRandom.nextInt(0, middleRange - 1);
          } else {
            // If fewer cards, select middle position
            selectedIndex = seededRandom.nextInt(0, validCards.length - 1);
          }
          
          const selected = validCards[selectedIndex];
          targetCard = selected.card;
          cardIndex = selected.index;
          console.log(`Found hotel card (selector: ${selector}, index: ${cardIndex}, selected ${selectedIndex + 1} of ${validCards.length} valid cards)`);
          console.log(`  Card text snippet: ${targetCard.textContent.substring(0, 150)}...`);
          break;
        }
      }
    }
  }
  
  // If still not found, try by random class names
  if (!targetCard) {
    console.log('Trying by random class names...');
    const randomClassSelectors = [
      'div[class*="a"]',
      'div[class*="b"]', 
      'div[class*="c"]',
      'div[class*="d"]',
      'div[class*="e"]',
      'div[class*="f"]'
    ];
    
    for (const selector of randomClassSelectors) {
      const cards = doc.querySelectorAll(selector);
      console.log(`Trying random class selector ${selector}: found ${cards.length} elements`);
      
      if (cards.length > 0) {
        for (let i = 0; i < cards.length; i++) {
          const card = cards[i];
          const text = card.textContent || '';
          
          // Stricter hotel card recognition
          if (text.length > 300 && text.length < 3000 && 
              text.includes('Hotel') && 
              (text.includes('$') || text.includes('Show prices')) &&
              (text.includes('Excellent') || text.includes('stars'))) {
            
            targetCard = card;
            cardIndex = i;
            console.log(`Found hotel card by random class (selector: ${selector}, index: ${i})`);
            console.log(`  Card text snippet: ${text.substring(0, 150)}...`);
            break;
          }
        }
        
        if (targetCard) break;
      }
    }
  }
  
  return { targetCard, cardIndex };
}

function extractRenderableCard(card) {
  if (!card) return null;
  return card.cloneNode(true);
}

function injectForceStyles(doc) {
  const style = doc.createElement('style');
  style.textContent = `
  #webarena-placement-header, #webarena-placement-banner, #webarena-placement-spotlight {
    display:block !important; visibility:visible !important; opacity:1 !important;
    box-sizing:border-box !important; max-width:1200px; margin:16px auto; padding:12px;
    background:#fff; border:1px dashed #999;
  }
  #webarena-placement-header *, #webarena-placement-banner *, #webarena-placement-spotlight * {
    visibility:visible !important; opacity:1 !important;
  }
  #webarena-placement-sidebar {
    display:block !important; visibility:visible !important; opacity:1 !important;
    box-sizing:border-box !important; width:100% !important; max-width:100% !important;
    margin:0 0 16px 0 !important; padding:0 !important; background:transparent !important;
    position:relative !important; float:none !important; clear:both !important;
    overflow:visible !important;
  }
  #webarena-placement-sidebar * { visibility:visible !important; opacity:1 !important; }
  #webarena-placement-sidebar [data-testid*="property-card"] {
    display:flex !important; flex-direction:column !important; width:100% !important;
  }
  #webarena-placement-sidebar .webarena-text-container {
    display: flex !important; flex-direction: column !important; align-items: stretch !important;
  }
  #webarena-placement-sidebar .webarena-reorganized-card { overflow: visible !important; }
  `;
  doc.head.appendChild(style);
}


function writeOut(html, outPath) {
  fse.ensureDirSync(path.dirname(outPath));
  // Write directly; scripts already stripped on read
  fs.writeFileSync(outPath, html, 'utf-8');
  console.log(`Exported: ${outPath}`);
}

function sanitizeColorForFilename(colorValue) {
  // Remove '#' character and return the hex value without it
  return colorValue.replace('#', '');
}

function createPositionVariations(dom, cfg, selectedHotelInfo) {
  const variations = [];
  
  // Header position
  const headerDom = new JSDOM(dom.serialize());
  const headerDoc = headerDom.window.document;
  injectForceStyles(headerDoc);
  
  // Use unified hotel card
  const headerTargetCard = selectedHotelInfo.card.cloneNode(true);
  
  // Remove card from original position
  const originalCardInHeader = headerDoc.querySelectorAll('[data-testid*="property-card"]')[selectedHotelInfo.cardIndex];
  if (originalCardInHeader) {
    originalCardInHeader.remove();
  }
  
  const headerContainer = headerDoc.createElement('div');
  headerContainer.id = 'webarena-placement-header';
  headerContainer.className = 'webarena-placement';
  headerContainer.style.cssText = 'background: #fff; padding: 15px; margin: 10px 0; border-radius: 8px; text-align: center; box-shadow: 0 4px 12px rgba(0,0,0,0.1);';
  
  // Move card to header position
  headerContainer.appendChild(headerTargetCard);
  
  // Try to find header position
  const headerSelectors = ['header', '.Header_root', '[data-testid*="header"]'];
  let headerAnchor = null;
  for (const selector of headerSelectors) {
    headerAnchor = headerDoc.querySelector(selector);
    if (headerAnchor) break;
  }
  
  if (headerAnchor) {
    headerAnchor.parentNode.insertBefore(headerContainer, headerAnchor);
  } else {
    headerDoc.body.insertBefore(headerContainer, headerDoc.body.firstChild);
  }
  
  variations.push({ name: 'header', html: headerDom.serialize() });
  
  // Banner position
  const bannerDom = new JSDOM(dom.serialize());
  const bannerDoc = bannerDom.window.document;
  injectForceStyles(bannerDoc);
  
  // Use unified hotel card
  const bannerTargetCard = selectedHotelInfo.card.cloneNode(true);
  
  // Remove card from original position
  const originalCardInBanner = bannerDoc.querySelectorAll('[data-testid*="property-card"]')[selectedHotelInfo.cardIndex];
  if (originalCardInBanner) {
    originalCardInBanner.remove();
  }
  
  const bannerContainer = bannerDoc.createElement('div');
  bannerContainer.id = 'webarena-placement-banner';
  bannerContainer.className = 'webarena-placement';
  bannerContainer.style.cssText = 'background: #fff; padding: 15px; margin: 10px 0; border-radius: 8px; text-align: center; box-shadow: 0 4px 12px rgba(0,0,0,0.1);';
  
  // Move card to banner position
  bannerContainer.appendChild(bannerTargetCard);
  
  // Try to find banner position - between search box and results text
  let bannerAnchor = null;
  
  // First try to find results text e.g. "Los Angeles: 2,158 properties found"
  const resultsTextElements = bannerDoc.querySelectorAll('*');
  for (let element of resultsTextElements) {
    if (element.textContent && element.textContent.includes('properties found') && 
        element.tagName !== 'HTML' && element.tagName !== 'BODY' && element.tagName !== 'HEAD') {
      bannerAnchor = element;
      console.log('Found results text element:', element.tagName, element.className);
      break;
    }
  }
  
  // If results text not found, try to find results container
  if (!bannerAnchor) {
    const searchResultsSelectors = [
      '[data-capla-component-boundary*="SearchResults"]',
      '[class*="searchresults"]',
      '[class*="results"]',
      '[class*="property-list"]'
    ];
    
    for (const selector of searchResultsSelectors) {
      bannerAnchor = bannerDoc.querySelector(selector);
      if (bannerAnchor) {
        console.log('Found results container:', selector, bannerAnchor.tagName, bannerAnchor.className);
        break;
      }
    }
  }
  
  // If still not found, try first main container after search box
  if (!bannerAnchor) {
    const searchBox = bannerDoc.querySelector('[data-testid*="searchbox"]');
    if (searchBox) {
      // Find first main div after search box
      let nextElement = searchBox.nextElementSibling;
      while (nextElement && nextElement.tagName !== 'DIV') {
        nextElement = nextElement.nextElementSibling;
      }
      if (nextElement) {
        bannerAnchor = nextElement;
      }
    }
  }
  
  if (bannerAnchor && bannerAnchor.parentNode) {
    bannerAnchor.parentNode.insertBefore(bannerContainer, bannerAnchor);
  } else {
    // If no suitable position, put at body start
    if (bannerDoc.body) {
      bannerDoc.body.insertBefore(bannerContainer, bannerDoc.body.firstChild);
    } else {
      // Last fallback: append to body
      bannerDoc.documentElement.appendChild(bannerContainer);
    }
  }
  
  variations.push({ name: 'banner', html: bannerDom.serialize() });
  
  // Spotlight position - in middle of search results list, highlighted
  const spotlightDom = new JSDOM(dom.serialize());
  const spotlightDoc = spotlightDom.window.document;
  injectForceStyles(spotlightDoc);
  
  // Use unified hotel card
  const spotlightTargetCard = selectedHotelInfo.card.cloneNode(true);
  
  // Remove card from original position
  const originalCardInSpotlight = spotlightDoc.querySelectorAll('[data-testid*="property-card"]')[selectedHotelInfo.cardIndex];
  if (originalCardInSpotlight) {
    originalCardInSpotlight.remove();
  }
  
  const spotlightContainer = spotlightDoc.createElement('div');
  spotlightContainer.id = 'webarena-placement-spotlight';
  spotlightContainer.className = 'webarena-placement';
  spotlightContainer.style.cssText = 'background: #f8f9fa; border: 2px solid #007bff; padding: 20px; margin: 20px auto; border-radius: 12px; text-align: center; max-width: 800px; box-shadow: 0 6px 20px rgba(0,123,255,0.15);';
  
  // Move card to spotlight position
  spotlightContainer.appendChild(spotlightTargetCard);
  
  // Try to find position in results list for spotlight
  let spotlightAnchor = null;
  
  // First try to find search results container
  const searchResultsSelectors = [
    '[data-capla-component-boundary*="SearchResults"]',
    '[class*="searchresults"]',
    '[class*="results"]',
    '[class*="property-list"]',
    '[class*="sr_property"]'
  ];
  
  for (const selector of searchResultsSelectors) {
    spotlightAnchor = spotlightDoc.querySelector(selector);
    if (spotlightAnchor) {
      console.log('Found results container for spotlight:', selector, spotlightAnchor.tagName, spotlightAnchor.className);
      break;
    }
  }
  
  if (spotlightAnchor) {
    // Get all hotel cards, insert before 12th card
    const allPropertyCards = spotlightDoc.querySelectorAll('[data-testid*="property-card"]');
    console.log(`Found ${allPropertyCards.length} hotel cards`);
    
    if (allPropertyCards.length > 0) {
      // Fixed at 16th card position (index 15), or middle if fewer than 10
      const targetIndex = Math.min(16, Math.floor(allPropertyCards.length / 2));
      const targetCard = allPropertyCards[targetIndex];
      
      if (targetCard && targetCard.parentNode) {
        targetCard.parentNode.insertBefore(spotlightContainer, targetCard);
        console.log(`Inserted spotlight before hotel card ${targetIndex + 1}`);
      } else {
        // If target position invalid, insert after last card
        const lastCard = allPropertyCards[allPropertyCards.length - 1];
        if (lastCard && lastCard.parentNode) {
          lastCard.parentNode.insertBefore(spotlightContainer, lastCard.nextSibling);
          console.log('Inserted spotlight after last hotel card');
        }
      }
    } else {
      spotlightAnchor.parentNode.insertBefore(spotlightContainer, spotlightAnchor);
      console.log('Inserted spotlight before results container');
    }
  } else {
    // If no suitable position, put at body start
    if (spotlightDoc.body) {
      spotlightDoc.body.insertBefore(spotlightContainer, spotlightDoc.body.firstChild);
      console.log('Inserted spotlight at body start');
    } else {
      // Last fallback: append to documentElement
      spotlightDoc.documentElement.appendChild(spotlightContainer);
      console.log('Inserted spotlight in documentElement');
    }
  }
  
  variations.push({ name: 'spotlight', html: spotlightDom.serialize() });

  // Sidebar position (merged from the legacy position-only sidebar generator)
  const sidebarDom = new JSDOM(dom.serialize());
  const sidebarDoc = sidebarDom.window.document;
  injectForceStyles(sidebarDoc);
  const originalCardInSidebar = sidebarDoc.querySelectorAll('[data-testid*="property-card"]')[selectedHotelInfo.cardIndex];
  if (originalCardInSidebar) {
    originalCardInSidebar.remove();
  }
  const sidebarTargetCard = selectedHotelInfo.card.cloneNode(true);
  const reorganizedCard = sidebarDoc.createElement('div');
  reorganizedCard.className = 'webarena-reorganized-card';
  reorganizedCard.style.cssText = `
    display: flex !important;
    flex-direction: column !important;
    width: 100% !important;
    background: #fff !important;
    border-radius: 8px !important;
    overflow: visible !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.1) !important;
  `;
  const imageContainer = sidebarDoc.createElement('div');
  imageContainer.style.cssText = 'width: 100% !important; order: 1 !important;';
  const imageLink = sidebarTargetCard.querySelector('a[href*="property"]') ||
                   sidebarTargetCard.querySelector('a[href*="hotel"]');
  if (imageLink && imageLink.querySelector('img')) {
    const clonedLink = imageLink.cloneNode(true);
    clonedLink.style.cssText = 'display: block !important; width: 100% !important; text-decoration: none !important;';
    clonedLink.querySelectorAll('img').forEach(img => {
      img.style.cssText = 'width: 100% !important; height: auto !important; display: block !important; object-fit: cover !important;';
    });
    imageContainer.appendChild(clonedLink);
  } else {
    sidebarTargetCard.querySelectorAll('img').forEach(img => {
      const cloned = img.cloneNode(true);
      cloned.style.cssText = 'width: 100% !important; height: auto !important; display: block !important; object-fit: cover !important;';
      imageContainer.appendChild(cloned);
    });
  }
  const textContainer = sidebarDoc.createElement('div');
  textContainer.className = 'webarena-text-container';
  textContainer.style.cssText = `
    width: 100% !important;
    padding: 12px 12px 16px 12px !important;
    order: 2 !important;
    box-sizing: border-box !important;
    display: flex !important;
    flex-direction: column !important;
    align-items: stretch !important;
    overflow: visible !important;
  `;
  const textCardClone = selectedHotelInfo.card.cloneNode(true);
  textCardClone.querySelectorAll('img').forEach(img => img.remove());
  const imgContainer = textCardClone.querySelector('[data-testid="property-card-container"]');
  if (imgContainer && !imgContainer.textContent.trim()) imgContainer.remove();
  while (textCardClone.firstChild) {
    textContainer.appendChild(textCardClone.firstChild);
  }
  const forceSingleColumn = (el) => {
    const style = el.style;
    const ds = (style.display || '').toLowerCase();
    if (ds.includes('flex')) {
      el.style.setProperty('flex-direction', 'column', 'important');
      el.style.setProperty('flex-wrap', 'nowrap', 'important');
      el.style.setProperty('width', '100%', 'important');
      el.style.setProperty('max-width', '100%', 'important');
    } else if (ds.includes('grid')) {
      el.style.setProperty('grid-template-columns', '1fr', 'important');
      el.style.setProperty('width', '100%', 'important');
    }
    Array.from(el.children).forEach(forceSingleColumn);
  };
  Array.from(textContainer.children).forEach(forceSingleColumn);
  textContainer.querySelectorAll('div, span').forEach(el => {
    const t = (el.textContent || '').trim();
    if (/^\d+\.\d+$/.test(t) && el.childElementCount <= 1) {
      el.classList.add('webarena-rating-score');
    }
  });
  reorganizedCard.appendChild(imageContainer);
  reorganizedCard.appendChild(textContainer);
  const sidebarContainer = sidebarDoc.createElement('div');
  sidebarContainer.id = 'webarena-placement-sidebar';
  sidebarContainer.className = 'webarena-placement';
  sidebarContainer.style.cssText = `
    display: block !important;
    width: 100% !important;
    max-width: 100% !important;
    margin: 0 0 16px 0 !important;
    padding: 0 !important;
    background: transparent !important;
    box-sizing: border-box !important;
  `;
  sidebarContainer.appendChild(reorganizedCard);
  let filterByEl = null;
  for (const el of sidebarDoc.querySelectorAll('*')) {
    const text = (el.textContent || '').trim();
    if ((text === 'Filter by' || text === 'Filter by:' || text.startsWith('Filter by')) && text.length < 80) {
      filterByEl = el;
      break;
    }
  }
  let insertAnchor = null;
  if (filterByEl) {
    let filterSection = filterByEl;
    for (let i = 0; i < 5; i++) {
      if (!filterSection.parentElement) break;
      filterSection = filterSection.parentElement;
      const sectionText = filterSection.textContent || '';
      if (sectionText.includes('Filter') || sectionText.includes('Your budget') || sectionText.includes('Deals')) {
        insertAnchor = filterSection;
        break;
      }
    }
    if (!insertAnchor) insertAnchor = filterSection;
  }
  if (insertAnchor && insertAnchor.parentNode) {
    insertAnchor.parentNode.insertBefore(sidebarContainer, insertAnchor);
    console.log('Card embedded in left sidebar (below Show on map, above Filter by)');
  } else {
    const leftCol = sidebarDoc.querySelector('[class*="filter"]') ||
                    sidebarDoc.querySelector('[class*="Filter"]') ||
                    sidebarDoc.querySelector('[data-testid*="filter"]');
    if (leftCol && leftCol.parentNode) {
      leftCol.parentNode.insertBefore(sidebarContainer, leftCol);
      console.log('Card inserted before left filter area');
    } else {
      const searchResults = sidebarDoc.querySelector('[data-capla-component-boundary*="SearchResults"]');
      if (searchResults && searchResults.parentNode) {
        searchResults.parentNode.insertBefore(sidebarContainer, searchResults);
        console.log('Filter by not found, card inserted before SearchResults');
      } else {
        sidebarDoc.body.insertBefore(sidebarContainer, sidebarDoc.body.firstChild);
        console.log('Fallback: card inserted at body start');
      }
    }
  }
  variations.push({ name: 'sidebar', html: sidebarDom.serialize() });
  
  return variations;
}

function createCardOrderVariations(dom, cfg, selectedHotelInfo) {
  const variations = [];
  
  // Order middle - move target card to 5th position
  const middleDom = new JSDOM(dom.serialize());
  const middleDoc = middleDom.window.document;
  injectForceStyles(middleDoc);
  
  const allHotelCards = Array.from(middleDoc.querySelectorAll('[data-testid="property-card"]'));
  console.log(`Found ${allHotelCards.length} hotel cards`);
  
  if (allHotelCards.length < 5) {
    console.log('Not enough hotel cards (need 5) to create order_middle variant');
  } else {
    const targetCard = allHotelCards[selectedHotelInfo.cardIndex];
    if (targetCard) {
      targetCard.remove();
      const remainingCards = Array.from(middleDoc.querySelectorAll('[data-testid="property-card"]'));
      if (remainingCards.length >= 4) {
        const targetPosition = remainingCards[4];
        if (targetPosition && targetPosition.parentElement) {
          targetPosition.parentElement.insertBefore(targetCard, targetPosition);
          console.log('Target card moved to 5th position');
        } else {
          const container = targetCard.parentElement || middleDoc.body;
          container.appendChild(targetCard);
          console.log('Target position not found, appended to container');
        }
      } else {
        const container = targetCard.parentElement || middleDoc.body;
        container.appendChild(targetCard);
        console.log('Not enough remaining cards, appended to container');
      }
    }
  }
  
  variations.push({ name: 'order_middle', html: middleDom.serialize() });
  middleDom.window.close();
  
  // Order last - move target card to last position
  const lastDom = new JSDOM(dom.serialize());
  const lastDoc = lastDom.window.document;
  injectForceStyles(lastDoc);
  
  const allHotelCardsLast = Array.from(lastDoc.querySelectorAll('[data-testid="property-card"]'));
  console.log(`Found ${allHotelCardsLast.length} hotel cards`);
  
  if (allHotelCardsLast.length < 2) {
    console.log('Not enough hotel cards (need 2) to create order_last variant');
  } else {
    const targetCardLast = allHotelCardsLast[selectedHotelInfo.cardIndex];
    if (targetCardLast) {
      targetCardLast.remove();
      const remainingCardsLast = Array.from(lastDoc.querySelectorAll('[data-testid="property-card"]'));
      if (remainingCardsLast.length > 0) {
        const lastCard = remainingCardsLast[remainingCardsLast.length - 1];
        if (lastCard && lastCard.parentElement) {
          lastCard.parentElement.insertBefore(targetCardLast, lastCard.nextSibling);
          console.log('Target card moved to last position');
        } else {
          const container = targetCardLast.parentElement || lastDoc.body;
          container.appendChild(targetCardLast);
          console.log('Target position not found, appended to container');
        }
      } else {
        const container = targetCardLast.parentElement || lastDoc.body;
        container.appendChild(targetCardLast);
        console.log('No remaining cards, appended to container');
      }
    }
  }
  
  variations.push({ name: 'order_last', html: lastDom.serialize() });
  lastDom.window.close();
  
  return variations;
}

function createBackgroundVariations(baseHtml, cfg, selectedHotelInfo) {
  const results = [];
  
  UNIFIED_VARIATIONS.backgroundColors.forEach(variation => {
    const dom = new JSDOM(baseHtml);
    const doc = dom.window.document;
    
    // Use unified hotel card
    const targetCard = doc.querySelectorAll('[data-testid*="property-card"]')[selectedHotelInfo.cardIndex];
    
    if (!targetCard) {
      console.log('Target card not found');
      dom.window.close();
      return;
    }
    
    console.log(`Applying background variant: ${variation.name}`);
    
    // Apply background color
    targetCard.style.setProperty('background-color', variation.value, 'important');
    console.log(`Applied background color: ${variation.value}`);
    
    const html = dom.serialize();
    results.push({ name: `background_${variation.name}`, html: html });
    console.log(`Created variant: background_${variation.name}`);
    
    dom.window.close();
  });
  
  return results;
}

function applyTextStyle(card, titleSelectors, prop, value) {
  let success = false;
  
  console.log(`Finding text elements, prop: ${prop}, value: ${value}`);
  
  // Try generic text element selectors first
  const genericSelectors = [
    'span', 'div', 'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'a', 'strong', 'em', 'b', 'i'
  ];
  
  // Try multiple selectors for text elements
  const textSelectors = [
    '[data-testid*="property-card"] span',
    '[data-testid*="property-card"] div',
    '[data-testid*="property-card"] p',
    '[data-testid*="property-card"] h1',
    '[data-testid*="property-card"] h2',
    '[data-testid*="property-card"] h3',
    '[data-testid*="property-card"] a',
    '[class*="title"]',
    '[class*="name"]',
    '[class*="price"]',
    '[class*="rating"]',
    '[class*="location"]'
  ];
  
  // Merge config selectors with defaults
  const allSelectors = [...genericSelectors, ...titleSelectors, ...textSelectors];
  
  for (const selector of allSelectors) {
    const elements = card.querySelectorAll(selector);
    if (elements.length > 0) {
      elements.forEach(el => {
        // Only apply to elements with text content
        if (el.textContent && el.textContent.trim().length > 0) {
          el.style.setProperty(prop, value, 'important');
          console.log(`Applied style to element: ${el.tagName}.${el.className} - ${prop} = ${value}`);
        }
      });
      success = true;
    }
  }
  
  if (!success) {
    console.log('No text elements found to apply style');
  }
  
  return success;
}

function applyImageStyle(doc, card, prop, value) {
  if (!card) return false;
  
  const images = card.querySelectorAll('img');
  if (images.length === 0) return false;
  
  images.forEach(img => {
    if (prop === 'filter') {
      img.style.setProperty('filter', value, 'important');
    } else if (prop === 'width') {
      img.style.setProperty('width', value, 'important');
      img.style.setProperty('height', 'auto', 'important');
    } else if (prop === 'height') {
      img.style.setProperty('height', value, 'important');
      img.style.setProperty('width', 'auto', 'important');
    }
  });
  
  return true;
}

function applyCardStyle(doc, card, prop, value) {
  if (!card) return false;
  
  if (prop === 'width') {
    card.style.setProperty('width', value, 'important');
  } else if (prop === 'height') {
    card.style.setProperty('height', value, 'important');
  } else if (prop === 'scale') {
    card.style.setProperty('transform', `scale(${value})`, 'important');
    card.style.setProperty('transform-origin', 'center', 'important');
  } else if (prop === 'filter') {
    card.style.setProperty('filter', value, 'important');
    const allElements = card.querySelectorAll('*');
    allElements.forEach(el => {
      el.style.setProperty('filter', 'inherit', 'important');
    });
  }
  
  return true;
}

function createStyleVariations(baseHtml, cfg, selectedHotelInfo, variationType, variations) {
  const results = [];
  
  variations.forEach(variation => {
    const dom = new JSDOM(baseHtml);
    const doc = dom.window.document;
    
    const targetCard = doc.querySelectorAll('[data-testid*="property-card"]')[selectedHotelInfo.cardIndex];
    if (!targetCard) {
      console.log('Selector matched no element');
      dom.window.close();
      return;
    }
    
    console.log(`Found target element, applying ${variationType} variant: ${variation.name || variation.value}`);
    
    let success = false;
    
    if (variationType === 'background') {
      targetCard.style.setProperty('background-color', variation.value, 'important');
      console.log(`Applied background color: ${variation.value}`);
      success = true;
    } else if (variationType === 'textColor') {
      success = applyTextStyle(targetCard, cfg.titleSelectors || [], 'color', variation.value);
    } else if (variationType === 'fontFamily') {
      success = applyTextStyle(targetCard, cfg.titleSelectors || [], 'font-family', variation.value);
    } else if (variationType === 'fontSize') {
      success = applyTextStyle(targetCard, cfg.titleSelectors || [], 'font-size', variation);
    }
    
    if (success) {
      const safeName = variation.name || variation.replace('px', 'px');
      const html = dom.serialize();
      results.push({ name: `${variationType}_${safeName}`, html: html });
      console.log(`Created variant: ${variationType}_${safeName}`);
    } else {
      console.log(`Failed to apply variant: ${variationType}_${variation.name || variation.value}`);
    }
    
    dom.window.close();
  });
  
  return results;
}

function createImageClarityVariations(baseHtml, cfg, selectedHotelInfo) {
  const results = [];
  
  UNIFIED_VARIATIONS.imageClarity.forEach(({ name, filter }) => {
    const dom = new JSDOM(baseHtml);
    const doc = dom.window.document;
    
    const targetCard = doc.querySelectorAll('[data-testid*="property-card"]')[selectedHotelInfo.cardIndex];
    if (!targetCard) return;
    
    const success = applyImageStyle(doc, targetCard, 'filter', filter);
    if (success) {
      const html = dom.serialize();
      results.push({ name: `image_clarity_${name}`, html: html });
    }
    
    dom.window.close();
  });
  
  return results;
}

function createCardClarityVariations(baseHtml, cfg, selectedHotelInfo) {
  const results = [];
  
  UNIFIED_VARIATIONS.cardClarity.forEach(({ name, filter }) => {
    const dom = new JSDOM(baseHtml);
    const doc = dom.window.document;
    
    const targetCard = doc.querySelectorAll('[data-testid*="property-card"]')[selectedHotelInfo.cardIndex];
    if (!targetCard) return;
    
    const success = applyCardStyle(doc, targetCard, 'filter', filter);
    if (success) {
      const html = dom.serialize();
      results.push({ name: `card_clarity_${name}`, html: html });
    }
    
    dom.window.close();
  });
  
  return results;
}

function createCardSizeVariations(baseHtml, cfg, selectedHotelInfo) {
  const results = [];
  
  UNIFIED_VARIATIONS.cardSizes.forEach(({ name, scale }) => {
    const dom = new JSDOM(baseHtml);
    const doc = dom.window.document;
    
    const targetCard = doc.querySelectorAll('[data-testid*="property-card"]')[selectedHotelInfo.cardIndex];
    if (!targetCard) return;
    
    const success = applyCardStyle(doc, targetCard, 'scale', scale);
    if (success) {
      const html = dom.serialize();
      results.push({ name: `card_size_${name}`, html: html });
    }
    
    dom.window.close();
  });
  
  return results;
}

(async () => {
  const cfg = loadConfig();
  const snapshotArgIdx = process.argv.findIndex(a => a === '--snapshot');
  const snapshotPath = snapshotArgIdx > -1 ? process.argv[snapshotArgIdx + 1] : cfg.snapshotPath;
  if (!snapshotPath) {
    console.error('Snapshot path required. Use --snapshot <path> or set snapshotPath in config_booking.json');
    process.exit(1);
  }

  const html = readSnapshot(snapshotPath);
  const dom = new JSDOM(html);
  const doc = dom.window.document;

  ensureBaseHref(dom, cfg);
  removeNodes(doc, cfg.ignoreSelectors || []);

  // Find target hotel card
  const { targetCard: originalCard, cardIndex } = findHotelCard(doc, cfg);
  
  if (!originalCard) {
    console.error('No hotel card found.');
    process.exit(1);
  }

  // Extract card for standalone rendering
  const renderableCard = extractRenderableCard(originalCard);
  if (!renderableCard) {
    console.error('Failed to extract card.');
    process.exit(1);
  }

  console.log(`Target hotel card index: ${cardIndex}`);
  
  // Store selected hotel card info for all variants
  const selectedHotelInfo = {
    card: originalCard,
    cardIndex: cardIndex,
    renderableCard: renderableCard
  };

  // Set output directory
  const outputArgIdx = process.argv.findIndex(a => a === '--output');
  const outputDirRaw = outputArgIdx > -1 ? process.argv[outputArgIdx + 1] : null;
  const outputDir = outputDirRaw ? path.resolve(process.cwd(), outputDirRaw) : path.resolve(__dirname, 'output_booking_SF');
  fse.ensureDirSync(outputDir);

  injectForceStyles(doc);

  const originalPath = path.join(outputDir, 'original.html');
  writeOut(dom.serialize(), originalPath);

  let totalVariations = 1;

  console.log('Creating position variants...');
  const positionVariations = createPositionVariations(dom, cfg, selectedHotelInfo);
  positionVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `position_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += positionVariations.length;

  console.log('Creating card order variants...');
  const cardOrderVariations = createCardOrderVariations(dom, cfg, selectedHotelInfo);
  cardOrderVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `order_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += cardOrderVariations.length;

  console.log('Creating background color variants...');
  const backgroundVariations = createBackgroundVariations(dom.serialize(), cfg, selectedHotelInfo);
  backgroundVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += backgroundVariations.length;

  console.log('Creating text color variants...');
  const textColorVariations = createStyleVariations(dom.serialize(), cfg, selectedHotelInfo, 'textColor', UNIFIED_VARIATIONS.textColors);
  textColorVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += textColorVariations.length;

  console.log('Creating font family variants...');
  const fontFamilyVariations = createStyleVariations(dom.serialize(), cfg, selectedHotelInfo, 'fontFamily', UNIFIED_VARIATIONS.fontFamilies);
  fontFamilyVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += fontFamilyVariations.length;

  console.log('Creating font size variants...');
  const fontSizeVariations = createStyleVariations(dom.serialize(), cfg, selectedHotelInfo, 'fontSize', UNIFIED_VARIATIONS.fontSizes);
  fontSizeVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += fontSizeVariations.length;

  console.log('Creating image clarity variants...');
  const imageClarityVariations = createImageClarityVariations(dom.serialize(), cfg, selectedHotelInfo);
  imageClarityVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += imageClarityVariations.length;

  console.log('Creating card clarity variants...');
  const cardClarityVariations = createCardClarityVariations(dom.serialize(), cfg, selectedHotelInfo);
  cardClarityVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += cardClarityVariations.length;

  console.log('Creating card size variants...');
  const cardSizeVariations = createCardSizeVariations(dom.serialize(), cfg, selectedHotelInfo);
  cardSizeVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += cardSizeVariations.length;

  console.log('Booking SF variant generation complete.');
  console.log(`Total files generated: ${totalVariations}`);
  console.log('Breakdown:');
  console.log('- Original: 1');
  console.log(`- Position variants: ${positionVariations.length}`);
  console.log(`- Card order variants: ${cardOrderVariations.length}`);
  console.log(`- Background color variants: ${backgroundVariations.length}`);
  console.log(`- Text color variants: ${textColorVariations.length}`);
  console.log(`- Font family variants: ${fontFamilyVariations.length}`);
  console.log(`- Font size variants: ${fontSizeVariations.length}`);
  console.log(`- Image clarity variants: ${imageClarityVariations.length}`);
  console.log(`- Card clarity variants: ${cardClarityVariations.length}`);
  console.log(`- Card size variants: ${cardSizeVariations.length}`);

  console.log('\nNote: Generated pages depend on online resources; network connection is needed for full display.');
  console.log('If pages appear incomplete, check network or use a full offline version.');
})();
