const fs = require('fs');
const fse = require('fs-extra');
const path = require('path');
const { JSDOM } = require('jsdom');

// Unified variation configuration - part 2
const UNIFIED_VARIATIONS_PART2 = {
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
    // { name: 'scale_1.5', scale: 1.5 }  // Commented out
  ],
  
  // Position variants (4)
  positions: [
    { name: 'header', description: 'Header position at top of page' },
    { name: 'banner', description: 'Spotlight banner in the middle of the page' },
    { name: 'sidebar', description: 'Sidebar position near the top of results' },
    { name: 'spotlight', description: 'Spotlight position in the middle of search results' }
  ],
  
  // Card order variants (2)
  cardOrders: [
    { name: 'middle', description: 'Move selected card to middle' },
    { name: 'last', description: 'Move selected card to last' }
  ]
};

function loadConfig(customPath) {
  const cfgPath = customPath || path.resolve(__dirname, 'config.json');
  const raw = fs.readFileSync(cfgPath, 'utf-8');
  return JSON.parse(raw);
}

function readSnapshot(snapshotPath) {
  const html = fs.readFileSync(snapshotPath, 'utf-8');
  return html.replace(/<script[\s\S]*?<\/script>/gi, '');
}

function ensureBaseHref(dom, cfg) {
  if (!cfg.addBaseTag) return;
  const doc = dom.window.document;
  let base = doc.querySelector('base');
  if (!base) {
    base = doc.createElement('base');
    doc.head.appendChild(base);
  }
  base.setAttribute('href', cfg.baseHref || 'https://www.expedia.com/');
}

function stripScriptsIfNeeded(html, cfg) {
  if (!cfg.stripScripts) return html;
  
  const ignoreSelectors = cfg.ignoreSelectors || [];
  const dom = new JSDOM(html);
  const doc = dom.window.document;
  
  ignoreSelectors.forEach(selector => {
    const elements = doc.querySelectorAll(selector);
    elements.forEach(el => el.remove());
  });
  
  const result = dom.serialize();
  dom.window.close();
  return result;
}

function removeNodes(doc, selectors) {
  selectors.forEach(selector => {
    const elements = doc.querySelectorAll(selector);
    elements.forEach(el => el.remove());
  });
}

function extractRenderableCard(card) {
  if (!card) return null;

  // Create a new document to contain this card
  const newDoc = new JSDOM('<!DOCTYPE html><html><head></head><body></body></html>').window.document;

  // Copy the card into the new document
  const clonedCard = card.cloneNode(true);
  newDoc.body.appendChild(clonedCard);
  
  return newDoc.body.innerHTML;
}

function injectForceStyles(doc) {
  const style = doc.createElement('style');
  style.textContent = `
    /* Force base styles so variants render correctly */
    * {
      box-sizing: border-box !important;
    }
    
    .uitk-card {
      position: relative !important;
    }
    
    /* Ensure z-index for fixed-position elements takes effect */
    [style*="position: fixed"] {
      z-index: 9999 !important;
    }
  `;
  doc.head.appendChild(style);
}

function writeOut(html, outPath) {
  fse.ensureDirSync(path.dirname(outPath));
  fs.writeFileSync(outPath, html, 'utf-8');
  console.log(`✅ Exported: ${outPath}`);
}

function sanitizeValueForFilename(value) {
  // Remove special characters and return clean value for filename
  return value.replace(/[^a-zA-Z0-9]/g, '');
}

function applyImageStyle(doc, targetCard, prop, value) {
  if (!targetCard) return false;
  
  const images = targetCard.querySelectorAll('img');
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

function applyCardStyle(doc, targetCard, prop, value) {
  if (!targetCard) return false;
  
  if (prop === 'width') {
    targetCard.style.setProperty('width', value, 'important');
  } else if (prop === 'height') {
    targetCard.style.setProperty('height', value, 'important');
  } else if (prop === 'scale') {
    // Use transform for scaling
    // For smaller scale values (<= 1.5), use uniform scaling
    // For larger values (> 1.5), still apply uniform scaling but allow more space
    targetCard.style.setProperty('transform-origin', 'center', 'important');
    
    // To avoid overlap, provide extra space around the scaled card.
    // transform: scale() does not change the layout box, so we manually add margin.
    
    if (value > 1) {
      let scaleX, scaleY;
      
      // Use uniform scaling
      scaleX = value;
      scaleY = value;
      targetCard.style.setProperty('transform', `scale(${value})`, 'important');
      
      // For Expedia hotel cards, typical dimensions are roughly:
      // - width: 400–600px (depends on layout)
      // - height: 300–500px (depends on content)
      // When scaling from center, each side needs extra (scale - 1) * base / 2 space.
      const baseMargin = 150; // Base margin in pixels
      const scaleFactor = 1.5; // Factor to tune margin size
      
      // Compute horizontal and vertical margins.
      // Using uniform scaling, so horizontal and vertical margins are the same.
      const marginValue = Math.ceil(baseMargin * (value - 1) * scaleFactor);
      
      // Set margins to give space to the scaled card.
      // When scaling from the center, all four sides use the same margin.
      targetCard.style.setProperty('margin-top', `${marginValue}px`, 'important');
      targetCard.style.setProperty('margin-bottom', `${marginValue}px`, 'important');
      targetCard.style.setProperty('margin-left', `${marginValue}px`, 'important');
      targetCard.style.setProperty('margin-right', `${marginValue}px`, 'important');
      
      // Ensure the card is not clipped
      targetCard.style.setProperty('overflow', 'visible', 'important');
      targetCard.style.setProperty('z-index', '10', 'important'); // Keep scaled card above others
      
      // Ensure parent containers do not clip content
      let parent = targetCard.parentElement;
      let depth = 0;
      while (parent && parent !== doc.body && depth < 5) {
        // Check and adjust parent overflow
        const computedStyle = doc.defaultView.getComputedStyle(parent);
        if (computedStyle.overflow === 'hidden' || computedStyle.overflow === 'clip' || 
            computedStyle.overflowY === 'hidden' || computedStyle.overflowX === 'hidden') {
          parent.style.setProperty('overflow', 'visible', 'important');
        }
        parent = parent.parentElement;
        depth++;
      }
    } else if (value < 1) {
      // When scale < 1, shrink the card with uniform scaling
      targetCard.style.setProperty('transform', `scale(${value})`, 'important');
      // But still ensure it is not clipped
      targetCard.style.setProperty('overflow', 'visible', 'important');
    }
  } else if (prop === 'filter') {
    targetCard.style.setProperty('filter', value, 'important');
    const allElements = targetCard.querySelectorAll('*');
    allElements.forEach(el => {
      el.style.setProperty('filter', 'inherit', 'important');
    });
  }
  
  return true;
}

function applyPositionStyle(doc, targetCard, position) {
  if (!targetCard) return false;
  
  // Remove any existing positioning styles
  targetCard.style.removeProperty('position');
  targetCard.style.removeProperty('top');
  targetCard.style.removeProperty('right');
  targetCard.style.removeProperty('left');
  targetCard.style.removeProperty('bottom');
  targetCard.style.removeProperty('z-index');
  targetCard.style.removeProperty('transform');
  targetCard.style.removeProperty('margin');
  targetCard.style.removeProperty('width');
  
  if (position === 'header') {
    // Header position - move card to the very top of the page (below site header, above other cards)
    const body = doc.body;
    const firstChild = body.firstChild;
    
    // Create header container
    const headerContainer = doc.createElement('div');
    headerContainer.style.setProperty('width', '100%', 'important');
    headerContainer.style.setProperty('padding', '10px', 'important');
    headerContainer.style.setProperty('margin-bottom', '20px', 'important');
    
    // Move target card into header container
    const clonedCard = targetCard.cloneNode(true);
    clonedCard.style.setProperty('width', '100%', 'important');
    clonedCard.style.setProperty('max-width', 'none', 'important');
    clonedCard.style.setProperty('margin', '0', 'important');
    clonedCard.style.setProperty('box-sizing', 'border-box', 'important');
    
    // Ensure text inside the card remains readable
    const textElements = clonedCard.querySelectorAll('.uitk-text, .uitk-type-300, .uitk-type-400, .uitk-type-500, h1, h2, h3, h4, h5, h6, p, span, div');
    textElements.forEach(el => {
      el.style.setProperty('word-wrap', 'break-word', 'important');
      el.style.setProperty('overflow-wrap', 'break-word', 'important');
      el.style.setProperty('white-space', 'normal', 'important');
    });
    
    headerContainer.appendChild(clonedCard);
    body.insertBefore(headerContainer, firstChild);
    
    // Hide original card in its old position
    targetCard.style.setProperty('display', 'none', 'important');
    
  } else if (position === 'banner') {
    // Banner position - place card in the middle of the hotel list as a spotlight banner
    const body = doc.body;
    
    // Create banner container - inspired by eBay implementation but adapted for Expedia styling
    const bannerContainer = doc.createElement('div');
    bannerContainer.style.setProperty('padding', '20px', 'important');
    bannerContainer.style.setProperty('margin', '20px auto', 'important');
    bannerContainer.style.setProperty('text-align', 'center', 'important');
    bannerContainer.style.setProperty('max-width', '800px', 'important');
    
    // No title heading; just the card itself
    
    // Move target card into banner container
    const clonedCard = targetCard.cloneNode(true);
    clonedCard.style.setProperty('width', '100%', 'important');
    clonedCard.style.setProperty('max-width', 'none', 'important');
    clonedCard.style.setProperty('margin', '0', 'important');
    clonedCard.style.setProperty('transform', 'scale(1.05)', 'important');
    clonedCard.style.setProperty('box-sizing', 'border-box', 'important');
    
    // Ensure text inside the card remains readable
    const textElements = clonedCard.querySelectorAll('.uitk-text, .uitk-type-300, .uitk-type-400, .uitk-type-500, h1, h2, h3, h4, h5, h6, p, span, div');
    textElements.forEach(el => {
      el.style.setProperty('word-wrap', 'break-word', 'important');
      el.style.setProperty('overflow-wrap', 'break-word', 'important');
      el.style.setProperty('white-space', 'normal', 'important');
    });
    
    bannerContainer.appendChild(clonedCard);
    
    // Find the hotel card list container and insert banner in the middle
    let bannerAnchor = null;
    
    // Try multiple possible selectors to locate the hotel list container
    const possibleSelectors = [
      'main',
      '[data-testid*="property"]',
      '[data-stid*="property-listing"]',
      '.uitk-card',
      '[class*="property"]',
      '[class*="listing"]',
      '[class*="results"]'
    ];
    
    for (const selector of possibleSelectors) {
      const elements = doc.querySelectorAll(selector);
      if (elements.length > 0) {
        // Find a container that holds multiple hotel cards
        for (const element of elements) {
          const cards = element.querySelectorAll('.uitk-card');
          if (cards.length > 2) { // Ensure there are enough cards to define a middle
            bannerAnchor = element;
            console.log(`✅ Found hotel list container: ${selector}, with ${cards.length} cards`);
            break;
          }
        }
        if (bannerAnchor) break;
      }
    }
    
    if (bannerAnchor) {
      // Insert banner in the middle of the hotel list
      const allCards = bannerAnchor.querySelectorAll('.uitk-card');
      if (allCards.length > 0) {
        const middleIndex = Math.floor(allCards.length / 2);
        const middleCard = allCards[middleIndex];
        
        // Ensure middleCard is a direct child of bannerAnchor
        if (middleCard.parentNode === bannerAnchor) {
          bannerAnchor.insertBefore(bannerContainer, middleCard);
          console.log(`✅ Inserted banner before card #${middleIndex + 1}`);
        } else {
          // If not a direct child, insert before its parent or fall back to container start
          const cardParent = middleCard.parentNode;
          if (cardParent && cardParent.parentNode === bannerAnchor) {
            bannerAnchor.insertBefore(bannerContainer, cardParent);
            console.log('✅ Inserted banner before card parent');
          } else {
            bannerAnchor.insertBefore(bannerContainer, bannerAnchor.firstChild);
            console.log('✅ Inserted banner at start of container (fallback)');
          }
        }
      } else {
        // If no cards found, insert at container start
        bannerAnchor.insertBefore(bannerContainer, bannerAnchor.firstChild);
        console.log('✅ Inserted banner at start of container');
      }
    } else {
      // If no suitable container is found, place banner in the middle of body
      const bodyChildren = Array.from(body.children);
      const middleIndex = Math.floor(bodyChildren.length / 2);
      if (middleIndex > 0 && middleIndex < bodyChildren.length) {
        body.insertBefore(bannerContainer, bodyChildren[middleIndex]);
        console.log('✅ Inserted banner into middle of body');
      } else {
        body.appendChild(bannerContainer);
        console.log('✅ Inserted banner at end of body');
      }
    }
    
    // Hide original card
    targetCard.style.setProperty('display', 'none', 'important');
    
  } else if (position === 'sidebar') {
    
    const body = doc.body;
    
    // Remove card from original position
    targetCard.remove();
    
    const sidebarContainer = doc.createElement('div');
    sidebarContainer.style.setProperty('padding', '20px', 'important');
    sidebarContainer.style.setProperty('margin', '20px auto', 'important');
    sidebarContainer.style.setProperty('text-align', 'center', 'important');
    sidebarContainer.style.setProperty('max-width', '800px', 'important');
    
    // Move target card into sidebar container
    const clonedCard = targetCard.cloneNode(true);
    clonedCard.style.setProperty('width', '100%', 'important');
    clonedCard.style.setProperty('max-width', 'none', 'important');
    clonedCard.style.setProperty('margin', '0', 'important');
    clonedCard.style.setProperty('transform', 'scale(1.05)', 'important');
    clonedCard.style.setProperty('box-sizing', 'border-box', 'important');
    
    // Ensure text inside the card remains readable
    const textElements = clonedCard.querySelectorAll('.uitk-text, .uitk-type-300, .uitk-type-400, .uitk-type-500, h1, h2, h3, h4, h5, h6, p, span, div');
    textElements.forEach(el => {
      el.style.setProperty('word-wrap', 'break-word', 'important');
      el.style.setProperty('overflow-wrap', 'break-word', 'important');
      el.style.setProperty('white-space', 'normal', 'important');
    });
    
    sidebarContainer.appendChild(clonedCard);
    
    // Find search results container (similar to Booking implementation)
    let sidebarAnchor = null;
    
    // Prefer the results container itself over the entire main element
    const searchResultsSelectors = [
      '[data-testid*="property-listing"]',
      '[class*="property-list"]',
      '[class*="results"]',
      '[class*="listing"]',
      '[class*="sr_property"]',
      '[data-stid*="property"]'
    ];
    
    for (const selector of searchResultsSelectors) {
      sidebarAnchor = doc.querySelector(selector);
      if (sidebarAnchor) {
        console.log(`✅ Found search results container for sidebar: ${selector}`);
        break;
      }
    }
    
    if (sidebarAnchor) {
      // Try inserting sidebar into the middle of the results list
      const resultsList = doc.querySelector('[class*="property"]') || doc.querySelector('[data-testid*="property-card"]') || doc.querySelector('.uitk-card');
      if (resultsList && resultsList.parentNode && resultsList.parentNode.children.length > 0) {
        const parentContainer = resultsList.parentNode;
        const middleIndex = Math.floor(parentContainer.children.length / 2);
        const middleItem = parentContainer.children[middleIndex];
        parentContainer.insertBefore(sidebarContainer, middleItem);
        console.log('✅ Inserted sidebar in the middle of search results');
      } else {
        // If no specific list is found, try to find hotel cards in the container
        const allCards = sidebarAnchor.querySelectorAll('.uitk-card');
        if (allCards.length > 2) {
          const middleIndex = Math.floor(allCards.length / 2);
          const middleCard = allCards[middleIndex];
          
          // Ensure middleCard is a direct child of sidebarAnchor
          if (middleCard.parentNode === sidebarAnchor) {
            sidebarAnchor.insertBefore(sidebarContainer, middleCard);
            console.log(`✅ Inserted sidebar before card #${middleIndex + 1} (true middle)`);
          } else {
            // If not a direct child, insert before its parent or fallback positions
            const cardParent = middleCard.parentNode;
            if (cardParent && cardParent.parentNode === sidebarAnchor) {
              sidebarAnchor.insertBefore(sidebarContainer, cardParent);
              console.log('✅ Inserted sidebar before card parent (true middle)');
            } else {
              const containerChildren = Array.from(sidebarAnchor.children);
              const insertIndex = Math.floor(containerChildren.length / 2);
              if (insertIndex > 0 && insertIndex < containerChildren.length) {
                sidebarAnchor.insertBefore(sidebarContainer, containerChildren[insertIndex]);
                console.log('✅ Inserted sidebar into middle of container (fallback)');
              } else {
                sidebarAnchor.insertBefore(sidebarContainer, sidebarAnchor.firstChild);
                console.log('✅ Inserted sidebar at start of container (fallback)');
              }
            }
          }
        } else {
          sidebarAnchor.parentNode.insertBefore(sidebarContainer, sidebarAnchor);
          console.log('✅ Inserted sidebar before results container');
        }
      }
    } else {
      // If no good container is found, place sidebar in the middle of body
      if (body) {
        const bodyChildren = Array.from(body.children);
        const insertIndex = Math.floor(bodyChildren.length / 2);
        if (insertIndex > 0 && insertIndex < bodyChildren.length) {
          body.insertBefore(sidebarContainer, bodyChildren[insertIndex]);
          console.log('✅ Inserted sidebar into middle of body');
        } else {
          body.appendChild(sidebarContainer);
          console.log('✅ Inserted sidebar at end of body');
        }
      }
    }
  } else if (position === 'spotlight') {
    // Spotlight position - place in the middle of the search results list, highly prominent
    const body = doc.body;
    
    // Remove card from original position
    targetCard.remove();
    
    const spotlightContainer = doc.createElement('div');
    spotlightContainer.id = 'webarena-placement-spotlight';
    spotlightContainer.className = 'webarena-placement';
    spotlightContainer.style.setProperty('background', '#f8f9fa', 'important');
    spotlightContainer.style.setProperty('border', '2px solid #007bff', 'important');
    spotlightContainer.style.setProperty('padding', '20px', 'important');
    spotlightContainer.style.setProperty('margin', '20px auto', 'important');
    spotlightContainer.style.setProperty('border-radius', '12px', 'important');
    spotlightContainer.style.setProperty('text-align', 'center', 'important');
    spotlightContainer.style.setProperty('max-width', '800px', 'important');
    spotlightContainer.style.setProperty('box-shadow', '0 6px 20px rgba(0,123,255,0.15)', 'important');
    
    // Move target card into spotlight container
    const clonedCard = targetCard.cloneNode(true);
    clonedCard.style.setProperty('width', '100%', 'important');
    clonedCard.style.setProperty('max-width', 'none', 'important');
    clonedCard.style.setProperty('margin', '0', 'important');
    clonedCard.style.setProperty('box-sizing', 'border-box', 'important');
    
    // Ensure text inside the card remains readable
    const textElements = clonedCard.querySelectorAll('.uitk-text, .uitk-type-300, .uitk-type-400, .uitk-type-500, h1, h2, h3, h4, h5, h6, p, span, div');
    textElements.forEach(el => {
      el.style.setProperty('word-wrap', 'break-word', 'important');
      el.style.setProperty('overflow-wrap', 'break-word', 'important');
      el.style.setProperty('white-space', 'normal', 'important');
    });
    
    spotlightContainer.appendChild(clonedCard);
    
    // Try to find a good insertion point for the spotlight within the results list
    let spotlightAnchor = null;
    
    // First try to find the right-hand hotel card list container,
    // i.e., the main area containing hotel cards
    const searchResultsSelectors = [
      'main',
      '[class*="uitk-layout-grid"][class*="has-columns"]',
      '[class*="property-list"]',
      '[class*="results"]',
      '[class*="listing"]',
      '[class*="hotel-results"]',
      '[data-testid*="property"]',
      '[data-stid*="property-listing"]'
    ];
    
    for (const selector of searchResultsSelectors) {
      const elements = doc.querySelectorAll(selector);
      console.log(`🔍 Trying selector ${selector}: found ${elements.length} elements`);
      
      for (const element of elements) {
        // Check whether the element contains hotel cards
        const cards = element.querySelectorAll('.uitk-card');
        console.log(`🔍 Element ${element.tagName} (${element.className.substring(0, 30)}...) has ${cards.length} cards`);
        
        // Skip hidden elements or those with too few cards
        if (cards.length > 5 && !element.classList.contains('is-visually-hidden') && !element.classList.contains('sf-hidden')) {
          spotlightAnchor = element;
          console.log(`✅ Found suitable search-results container: ${selector}, with ${cards.length} cards`);
          break;
        }
      }
      if (spotlightAnchor) break;
    }
    
    if (spotlightAnchor) {
      console.log(`🔍 Analyzing spotlight insertion position, container type: ${spotlightAnchor.tagName}, class: ${spotlightAnchor.className}`);
      
      // Directly look for all hotel cards within spotlightAnchor
      const allCards = spotlightAnchor.querySelectorAll('.uitk-card');
      console.log(`🔍 Found ${allCards.length} cards inside spotlightAnchor`);
      
      let hotelListContainer = null;
      let maxCardsInContainer = 0;
      
      if (allCards.length > 0) {
        // Find the direct parent container that holds the most hotel cards
        const containerCandidates = new Map();
        
        allCards.forEach((card, index) => {
          let parent = card.parentNode;
          // Walk up to 3 levels to find the most suitable container
          for (let level = 0; level < 3 && parent && parent !== spotlightAnchor; level++) {
            if (!containerCandidates.has(parent)) {
              const cardsInParent = parent.querySelectorAll('.uitk-card');
              containerCandidates.set(parent, {
                count: cardsInParent.length,
                level: level,
                element: parent
              });
              console.log(`🔍 Candidate container level-${level}: ${parent.tagName} (${parent.className.substring(0, 50)}...) has ${cardsInParent.length} cards`);
            }
            parent = parent.parentNode;
          }
        });
        
        // Choose best container: prioritize card count, then depth
        let bestCandidate = null;
        let bestScore = 0;
        
        for (const [container, info] of containerCandidates) {
          // Scoring: more cards is better, shallower level is slightly preferred
          const score = info.count * 10 - info.level;
          console.log(`🔍 Container score: ${score} (${info.count} cards, level ${info.level})`);
          
          if (score > bestScore && info.count > 2) {
            bestScore = score;
            bestCandidate = info;
            hotelListContainer = container;
          }
        }
        
        if (hotelListContainer) {
          console.log(`✅ Chosen best hotel-list container with ${bestCandidate.count} cards, level ${bestCandidate.level}`);
          
          // Refresh cards list from chosen container to get latest DOM state
          const cards = hotelListContainer.querySelectorAll('.uitk-card');
          console.log(`🔍 Refreshed card count in container: ${cards.length}`);
          
          if (cards.length > 0) {
            // Insert at the 6th position (index 5)
            const targetPositionIndex = Math.min(5, cards.length - 1);
            const targetCard = cards[targetPositionIndex];
            
            console.log(`🔍 Trying to insert spotlight before card #${targetPositionIndex + 1}, tag: ${targetCard.tagName}`);
            
            // Ensure targetCard still exists in DOM
            if (targetCard && targetCard.parentNode) {
              try {
                // Method 1: insert directly before targetCard
                const parent = targetCard.parentNode;
                parent.insertBefore(spotlightContainer, targetCard);
                console.log(`✅ Spotlight inserted at hotel list card #${targetPositionIndex + 1}`);
              } catch (error) {
                console.log(`⚠️ Method 1 failed: ${error.message}`);
                
                try {
                  // Method 2: insert near the middle of the parent container
                  const parent = targetCard.parentNode;
                  const parentChildren = Array.from(parent.children);
                  const cardIndex = parentChildren.indexOf(targetCard);
                  
                  if (cardIndex > 0) {
                    const insertIndex = Math.floor(cardIndex / 2);
                    const insertBeforeElement = parentChildren[insertIndex];
                    parent.insertBefore(spotlightContainer, insertBeforeElement);
                    console.log(`✅ Spotlight inserted at parent child #${insertIndex + 1}`);
                  } else {
                    parent.insertBefore(spotlightContainer, targetCard);
                    console.log('✅ Spotlight inserted at first position in parent');
                  }
                } catch (error2) {
                  console.log(`⚠️ Method 2 failed: ${error2.message}, trying container middle`);
                  
                  try {
                    // Method 3: insert at the 6th child of hotelListContainer
                    const parentChildren = Array.from(hotelListContainer.children);
                    const targetChildIndex = Math.min(5, parentChildren.length - 1);
                    const targetChild = parentChildren[targetChildIndex];
                    
                    hotelListContainer.insertBefore(spotlightContainer, targetChild);
                    console.log(`✅ Spotlight inserted at container child #${targetChildIndex + 1}`);
                  } catch (error3) {
                    console.log(`⚠️ Method 3 failed: ${error3.message}, using fallback`);
                    hotelListContainer.appendChild(spotlightContainer);
                    console.log('✅ Spotlight appended to end of hotel list (fallback)');
                  }
                }
              }
            } else {
              console.log('⚠️ Target card missing, trying to insert at 6th position directly on container');
              
              try {
                const parentChildren = Array.from(hotelListContainer.children);
                const targetChildIndex = Math.min(5, parentChildren.length - 1);
                const targetChild = parentChildren[targetChildIndex];
                
                hotelListContainer.insertBefore(spotlightContainer, targetChild);
                console.log(`✅ Spotlight inserted at container child #${targetChildIndex + 1}`);
              } catch (error) {
                console.log(`⚠️ Insert into container middle failed: ${error.message}, appending to end`);
                hotelListContainer.appendChild(spotlightContainer);
                console.log('✅ Spotlight appended to end of hotel list');
              }
            }
          } else {
            console.log('⚠️ No cards found in container, appending spotlight to end');
            hotelListContainer.appendChild(spotlightContainer);
            console.log('✅ Spotlight appended to end of hotel list');
          }
        }
      }
      
      if (!hotelListContainer) {
        // If still no suitable container, use fallback on allCards
        console.log('⚠️ No suitable hotel-list container found, using fallback');
        
        if (allCards.length > 2) {
          // Insert at the 6th card (index 5)
          const targetCardIndex = Math.min(5, allCards.length - 1);
          const targetCard = allCards[targetCardIndex];
          
          if (targetCard && targetCard.parentNode) {
            targetCard.parentNode.insertBefore(spotlightContainer, targetCard);
            console.log(`✅ Spotlight inserted as sibling before card #${targetCardIndex + 1}`);
          } else {
            spotlightAnchor.appendChild(spotlightContainer);
            console.log('✅ Spotlight appended to end of spotlightAnchor');
          }
        } else {
          spotlightAnchor.appendChild(spotlightContainer);
          console.log('✅ Spotlight appended to end of spotlightAnchor (no cards)');
        }
      }
    } else {
      // If no good position is found, place spotlight in the middle of body
      if (body) {
        const bodyChildren = Array.from(body.children);
        const insertIndex = Math.floor(bodyChildren.length / 2);
        if (insertIndex > 0 && insertIndex < bodyChildren.length) {
          body.insertBefore(spotlightContainer, bodyChildren[insertIndex]);
          console.log('✅ Inserted spotlight into middle of body');
        } else {
          body.appendChild(spotlightContainer);
          console.log('✅ Inserted spotlight at end of body');
        }
      }
    }
  }
  
  return true;
}

function createImageClarityVariations(baseHtml, cfg, targetHotelName) {
  const results = [];
  
  UNIFIED_VARIATIONS_PART2.imageClarity.forEach(({ name, filter }) => {
    const dom = new JSDOM(baseHtml);
    const doc = dom.window.document;
    
    // Re-locate target hotel card
    let targetCard = null;
    const allCards = doc.querySelectorAll('.uitk-card');
    
    for (let i = 0; i < allCards.length; i++) {
      const card = allCards[i];
      const cardText = card.textContent || '';
      
      if (cardText.includes(targetHotelName) && 
          cardText.length > 200 && 
          cardText.length < 2000 &&
          !cardText.includes('Heritage Hotel') &&
          !cardText.includes('The Belvedere')) {
        targetCard = card;
        break;
      }
    }
    
    if (targetCard) {
      const success = applyImageStyle(doc, targetCard, 'filter', filter);
      if (success) {
        const html = dom.serialize();
        results.push({ name: `image_clarity_${name}`, html: html });
        console.log(`✅ Created image clarity variation: image_clarity_${name}`);
      }
    }
    
    dom.window.close();
  });
  
  return results;
}

function createCardClarityVariations(baseHtml, cfg, targetHotelName) {
  const results = [];
  
  UNIFIED_VARIATIONS_PART2.cardClarity.forEach(({ name, filter }) => {
    const dom = new JSDOM(baseHtml);
    const doc = dom.window.document;
    
    // Re-locate target hotel card
    let targetCard = null;
    const allCards = doc.querySelectorAll('.uitk-card');
    
    for (let i = 0; i < allCards.length; i++) {
      const card = allCards[i];
      const cardText = card.textContent || '';
      
      if (cardText.includes(targetHotelName) && 
          cardText.length > 200 && 
          cardText.length < 2000 &&
          !cardText.includes('Heritage Hotel') &&
          !cardText.includes('The Belvedere')) {
        targetCard = card;
        break;
      }
    }
    
    if (targetCard) {
      const success = applyCardStyle(doc, targetCard, 'filter', filter);
      if (success) {
        const html = dom.serialize();
        results.push({ name: `card_clarity_${name}`, html: html });
        console.log(`✅ Created card clarity variation: card_clarity_${name}`);
      }
    }
    
    dom.window.close();
  });
  
  return results;
}

function createCardSizeVariations(baseHtml, cfg, targetHotelName) {
  const results = [];
  
  UNIFIED_VARIATIONS_PART2.cardSizes.forEach(({ name, scale }) => {
    const dom = new JSDOM(baseHtml);
    const doc = dom.window.document;
    
    // Re-locate target hotel card
    let targetCard = null;
    const allCards = doc.querySelectorAll('.uitk-card');
    
    for (let i = 0; i < allCards.length; i++) {
      const card = allCards[i];
      const cardText = card.textContent || '';
      
      if (cardText.includes(targetHotelName) && 
          cardText.length > 200 && 
          cardText.length < 2000 &&
          !cardText.includes('Heritage Hotel') &&
          !cardText.includes('The Belvedere')) {
        targetCard = card;
        break;
      }
    }
    
    if (targetCard) {
      const success = applyCardStyle(doc, targetCard, 'scale', scale);
      if (success) {
        const html = dom.serialize();
        results.push({ name: `card_size_${name}`, html: html });
        console.log(`✅ Created card size variation: card_size_${name}`);
      }
    }
    
    dom.window.close();
  });
  
  return results;
}

function createPositionVariations(baseHtml, cfg, targetHotelName) {
  const results = [];
  
  UNIFIED_VARIATIONS_PART2.positions.forEach(({ name, description }) => {
    const dom = new JSDOM(baseHtml);
    const doc = dom.window.document;
    
    // Re-locate target hotel card
    let targetCard = null;
    const allCards = doc.querySelectorAll('.uitk-card');
    
    for (let i = 0; i < allCards.length; i++) {
      const card = allCards[i];
      const cardText = card.textContent || '';
      
      if (cardText.includes(targetHotelName) && 
          cardText.length > 200 && 
          cardText.length < 2000 &&
          !cardText.includes('Heritage Hotel') &&
          !cardText.includes('The Belvedere')) {
        targetCard = card;
        break;
      }
    }
    
    if (targetCard) {
      const success = applyPositionStyle(doc, targetCard, name);
      if (success) {
        const html = dom.serialize();
        
        // Use a clean output name consistent with config
        let outputName = name;
        
        results.push({ name: `position_${outputName}`, html: html });
        console.log(`✅ Created position variation: position_${outputName} (${description})`);
      }
    }
    
    dom.window.close();
  });
  
  return results;
}

function createCardOrderVariations(baseHtml, cfg, targetHotelName) {
  const results = [];
  
  UNIFIED_VARIATIONS_PART2.cardOrders.forEach(({ name, description }) => {
    const dom = new JSDOM(baseHtml);
    const doc = dom.window.document;
    
    // Re-locate target hotel card
    let targetCard = null;
    let targetIndex = -1;
    
    // Method 1: use .uitk-card selector to find all cards
    const allCards = doc.querySelectorAll('.uitk-card');
    
    for (let i = 0; i < allCards.length; i++) {
      const card = allCards[i];
      const cardText = card.textContent || '';
      
      // Find cards that contain the target hotel name
      if (cardText.includes(targetHotelName) && 
          cardText.length > 200 && 
          cardText.length < 2000) {
        targetCard = card;
        targetIndex = i;
        console.log(`✅ Found target hotel card (uitk-card ${i}): ${targetHotelName}`);
        break;
      }
    }
    
    if (!targetCard) {
      console.log(`❌ Target hotel not found: ${targetHotelName}`);
      dom.window.close();
      return;
    }

    console.log(`✅ Ready to apply order variation: ${name} to target card`);
    
    // Find search-results container
    const resultsContainer = doc.querySelector('.uitk-layout-grid') || 
                           doc.querySelector('[data-stid*="property-listing"]') ||
                           doc.querySelector('.uitk-card').parentElement;
    
    if (!resultsContainer) {
      console.log('❌ Search-results container not found');
      dom.window.close();
      return;
    }
    
    // Get all hotel cards (before removing target card)
    const allHotelCards = Array.from(resultsContainer.querySelectorAll('.uitk-card')).filter(card => {
      const cardText = card.textContent || '';
      return cardText.length > 200 && cardText.length < 2000;
    });
    
    console.log(`🔍 Found ${allHotelCards.length} hotel cards`);
    
    if (allHotelCards.length < 2) {
      console.log('❌ Not enough hotel cards to adjust order');
      dom.window.close();
      return;
    }
    
    // Decide target position before removing the card
    let targetPosition = null;
    
    // Exclude the target card itself so we only consider other hotels
    const otherHotelCards = allHotelCards.filter(card => card !== targetCard);
    console.log(`🔍 After excluding target card, ${otherHotelCards.length} hotel cards remain`);
    
    if (name === 'middle') {
      // Move to 6th position
      console.log('🔍 Moving to 6th position...');
      if (otherHotelCards.length >= 6) {
        targetPosition = otherHotelCards[5]; // 6th position (index 5)
        console.log('✅ Target position chosen: 6th card');
      }
    } else if (name === 'last') {
      // Move to 26th position (near the end but not last)
      console.log('🔍 Moving to 26th position...');
      if (otherHotelCards.length >= 26) {
        targetPosition = otherHotelCards[25]; // 26th position (index 25)
        console.log('✅ Target position chosen: 26th card');
      }
    }
    
    // Before removing the card, record its parent (for spacing structure)
    const targetCardParent = targetCard.parentElement;
    const hasSpacingParent = targetCardParent && (
      targetCardParent.classList.contains('uitk-spacing') ||
      targetCardParent.className.includes('uitk-spacing')
    );
    
    // Remove card from its original position
    targetCard.remove();
    
    try {
      if (targetPosition) {
        console.log(`🔍 Target position parent: ${targetPosition.parentElement?.tagName}`);
        console.log(`🔍 Results container: ${resultsContainer.tagName}`);
        console.log(`🔍 Is target position inside results container: ${targetPosition.parentElement === resultsContainer}`);
        
        // Get parent container of target position (to preserve spacing structure)
        const targetPositionParent = targetPosition.parentElement;
        const targetHasSpacingParent = targetPositionParent && (
          targetPositionParent.classList.contains('uitk-spacing') ||
          targetPositionParent.className.includes('uitk-spacing')
        );
        
        // If target position is not directly under results container, use its actual parent
        if (targetPosition.parentElement !== resultsContainer) {
          const correctParent = targetPosition.parentElement;
          if (correctParent) {
            // If target position has a spacing parent, insert within that same parent
            if (targetHasSpacingParent && targetPositionParent) {
              targetPositionParent.insertBefore(targetCard, targetPosition);
              console.log('✅ Moved Montage Big Sky to target using spacing parent');
            } else {
              correctParent.insertBefore(targetCard, targetPosition);
              console.log('✅ Moved Montage Big Sky to target using correct parent');
            }
          } else {
            resultsContainer.appendChild(targetCard);
            console.log('✅ Fallback: appended target card to end of container');
          }
        } else {
          // If target is already under resultsContainer, but has a spacing parent, insert into that parent
          if (targetHasSpacingParent && targetPositionParent) {
            targetPositionParent.insertBefore(targetCard, targetPosition);
            console.log('✅ Moved Montage Big Sky to target using spacing parent');
          } else {
            resultsContainer.insertBefore(targetCard, targetPosition);
            console.log('✅ Moved Montage Big Sky to target position');
          }
        }
      } else {
        // Fallback: move to last position
        resultsContainer.appendChild(targetCard);
        console.log('✅ Fallback: moved target card to end');
      }
    } catch (error) {
      console.log(`❌ Insert failed: ${error.message}`);
      // Fallback: append to end of container
      resultsContainer.appendChild(targetCard);
      console.log('✅ Fallback: appended target card to end of container');
    }
    
    results.push({
      name: `order_${name}`,
      html: dom.serialize(),
      description: description
    });
    
    dom.window.close();
  });
  
  return results;
}

(async () => {
  const cfg = loadConfig();
  const snapshotArgIdx = process.argv.findIndex(a => a === '--snapshot');
  const snapshotPath = snapshotArgIdx > -1 ? process.argv[snapshotArgIdx + 1] : cfg.snapshotPath;
  if (!snapshotPath) {
    console.error('❌ Snapshot path not provided. Use --snapshot or set snapshotPath in config.json.');
    process.exit(1);
  }

  const rawHtml = readSnapshot(snapshotPath);
  const html = stripScriptsIfNeeded(rawHtml, cfg);

  const dom = new JSDOM(html);
  const doc = dom.window.document;

  ensureBaseHref(dom, cfg);
  removeNodes(doc, cfg.ignoreSelectors || []);

  // Precisely locate the Montage Big Sky hotel card
  const targetHotelName = "Montage Big Sky";
  let originalCard = null;
  let cardIndex = -1;

  console.log(`🔍 Searching for target hotel: ${targetHotelName}`);
  
  // Method 1: try .uitk-card selector to find all cards
  const allCards = doc.querySelectorAll('.uitk-card');
  console.log(`🔍 Found ${allCards.length} uitk-card elements`);
  
  let heritageCard = null;
  for (let i = 0; i < allCards.length; i++) {
    const card = allCards[i];
    const cardText = card.textContent || '';
    
    // Find cards that contain "Montage Big Sky" and not other hotels
    if (cardText.includes(targetHotelName) && 
        cardText.length > 200 && 
        cardText.length < 2000 &&
        !cardText.includes('Heritage Hotel') &&
        !cardText.includes('The Belvedere')) {
      heritageCard = card;
      console.log(`✅ Found Montage Big Sky card (uitk-card ${i})`);
      console.log(`   Text length: ${cardText.length}`);
      console.log(`   Text snippet: ${cardText.substring(0, 100)}...`);
      break;
    }
  }
  
  if (heritageCard) {
    originalCard = heritageCard;
    cardIndex = 0;
  } else {
    // Method 2: if not found, try property-listing selector
    const propertyCards = doc.querySelectorAll('[data-stid*="property-listing"]');
    console.log(`🔍 Found ${propertyCards.length} property-listing elements`);
    
    for (let i = 0; i < propertyCards.length; i++) {
      const card = propertyCards[i];
      const cardText = card.textContent || '';
      
      if (cardText.includes(targetHotelName)) {
        console.log(`✅ Found target hotel in property-listing: ${targetHotelName}`);
        console.log(`   Card index: ${i}`);
        console.log(`   Card text length: ${cardText.length}`);
        
        // Inside large card, search for specific Montage Big Sky sub-element
        const heritageElements = card.querySelectorAll('*');
        
        for (const element of heritageElements) {
          const elementText = element.textContent || '';
          // Look for elements containing "Montage Big Sky" with reasonable text length
          if (elementText.includes(targetHotelName) && 
              elementText.length > 100 && 
              elementText.length < 10000) {
            originalCard = element;
            cardIndex = i;
            console.log(`✅ Found Montage Big Sky sub-element, text length: ${elementText.length}`);
            break;
          }
        }
        
        if (!originalCard) {
          originalCard = card;
          cardIndex = i;
          console.log('⚠️  Using large card as target (may include other hotels)');
        }
        break;
      }
    }
  }

  // If still not found, try additional selectors
  if (!originalCard) {
    console.log('🔍 Not found via property-listing, trying additional selectors...');
    const possibleSelectors = [
      '.uitk-card[data-stid*="hotel"]',
      '.uitk-card[data-stid*="property"]',
      '.uitk-card[data-stid*="listing"]',
      '.uitk-card',
      '[class*="hotel"][class*="card"]',
      '[class*="property"][class*="card"]',
      '[class*="listing"][class*="card"]'
    ];

    for (const selector of possibleSelectors) {
      const cards = doc.querySelectorAll(selector);
      console.log(`🔍 Trying selector ${selector}: found ${cards.length} elements`);
      
      for (let i = 0; i < cards.length; i++) {
        const card = cards[i];
        const cardText = card.textContent || '';
        
        if (cardText.includes(targetHotelName)) {
          originalCard = card;
          cardIndex = i;
          console.log(`✅ Found target hotel via selector ${selector}: ${targetHotelName}`);
          break;
        }
      }
      
      if (originalCard) break;
    }
  }

  if (!originalCard) {
    console.error('❌ Could not find any hotel card.');
    process.exit(1);
  }
  
  // Extract a standalone renderable card
  const renderableCard = extractRenderableCard(originalCard);
  if (!renderableCard) {
    console.error('❌ Failed to extract card.');
    process.exit(1);
  }

  console.log(`🎯 Target hotel: ${targetHotelName}, card index: ${cardIndex}`);

  // Configure output directory
  const outputDir = path.resolve(__dirname, 'output_expedia2_unified_complete');
  fse.ensureDirSync(outputDir);

  // Apply forced styles to base page
  injectForceStyles(doc);

  // Create original file
  const originalPath = path.join(outputDir, 'original.html');
  writeOut(dom.serialize(), originalPath);

  let totalVariations = 1; // original

  // Create image clarity variants
  console.log('🎨 Creating image clarity variants...');
  const imageClarityVariations = createImageClarityVariations(dom.serialize(), cfg, targetHotelName);
  imageClarityVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += imageClarityVariations.length;

  // Create card clarity variants
  console.log('🎨 Creating card clarity variants...');
  const cardClarityVariations = createCardClarityVariations(dom.serialize(), cfg, targetHotelName);
  cardClarityVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += cardClarityVariations.length;

  // Create card size variants
  console.log('🎨 Creating card size variants...');
  const cardSizeVariations = createCardSizeVariations(dom.serialize(), cfg, targetHotelName);
  cardSizeVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += cardSizeVariations.length;

  // Create position variants
  console.log('🎨 Creating position variants...');
  const positionVariations = createPositionVariations(dom.serialize(), cfg, targetHotelName);
  positionVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += positionVariations.length;

  // Create card order variants
  console.log('🎨 Creating card order variants...');
  const cardOrderVariations = createCardOrderVariations(dom.serialize(), cfg, targetHotelName);
  cardOrderVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += cardOrderVariations.length;

  console.log('🎉 Expedia part 2 variants generation completed');
  console.log(`Total files generated: ${totalVariations}`);
  console.log('Variant breakdown:');
  console.log(`- Original: 1`);
  console.log(`- Image clarity variants: ${imageClarityVariations.length}`);
  console.log(`- Card clarity variants: ${cardClarityVariations.length}`);
  console.log(`- Card size variants: ${cardSizeVariations.length}`);
  console.log(`- Position variants: ${positionVariations.length}`);
  console.log(`- Card order variants: ${cardOrderVariations.length}`);
})();
