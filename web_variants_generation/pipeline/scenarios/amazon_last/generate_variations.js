const fs = require('fs');
const fse = require('fs-extra');
const path = require('path');
const { JSDOM } = require('jsdom');

// Unified variation config
const UNIFIED_VARIATIONS = {
  // Position variants
  positions: [
    { name: 'banner', description: 'Banner at top of page' },
    { name: 'floating', description: 'Floating card at right side' },
    { name: 'spotlight', description: 'Spotlight in middle of search results' }
  ],
  
  // Card order variants (3)
  cardOrders: [
    { name: 'first', description: 'Move selected card to first' },
    { name: 'middle', description: 'Move selected card to middle' },
    { name: 'last', description: 'Move selected card to last' }
  ],
  
  // Background color variants (11)
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
    { name: '9c27b0', value: '#9c27b0' }  // purple
  ],
  
  // Text color variants (5)
  textColors: [
    { name: '111111', value: '#111111' },
    { name: '0d6efd', value: '#0d6efd' },
    { name: 'dc3545', value: '#dc3545' },
    { name: '198754', value: '#198754' },
    { name: '6f42c1', value: '#6f42c1' }
  ],
  
  // Font variants (8)
  fontFamilies: [
    { name: 'arial', value: 'Arial, sans-serif' },
    { name: 'helvetica', value: 'Helvetica, sans-serif' },
    { name: 'courier', value: 'Courier New, monospace' },
    { name: 'georgia', value: 'Georgia, serif' },
    { name: 'times', value: 'Times New Roman, serif' },
    { name: 'verdana', value: 'Verdana, sans-serif' },
    { name: 'lucida', value: 'Lucida Console, monospace' },
    { name: 'comic', value: 'Comic Sans MS, cursive' }
  ],
  
  // Font size variants (8)
  fontSizes: ['14px', '16px', '18px', '20px', '22px', '24px', '26px', '28px'],
  
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
  
  // Card size variants (6)
  cardSizes: [
    { name: 'scale_0.8', scale: 0.8 },
    { name: 'scale_1.0', scale: 1.0 },
    { name: 'scale_1.2', scale: 1.2 },
    { name: 'scale_1.5', scale: 1.5 },
    { name: 'scale_1.75', scale: 1.75 },
    { name: 'scale_2.0', scale: 2.0 }
  ]
};

function loadConfig(customPath) {
  const cfgPath = customPath || path.resolve(__dirname, 'config_amazon.json');
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
  base.setAttribute('href', cfg.baseHref || 'https://www.amazon.com/');
}

function stripScriptsIfNeeded(html, cfg) {
  if (!cfg.stripScripts) return html;
  return html.replace(/<script[\s\S]*?<\/script>/gi, '');
}

function removeNodes(doc, selectors = []) {
  selectors.forEach(sel => {
    doc.querySelectorAll(sel).forEach(n => n.remove());
  });
}

function pickTargetCard(doc, cfg) {
  const { target } = cfg;
  let node = doc.querySelector(target.selector);
  if (!node) node = doc.querySelector(target.fallback);
  return node;
}

function extractRenderableCard(card) {
  if (!card) return null;
  const sc = card.querySelector('.s-card-container') || card.querySelector('.puis-card-container');
  if (sc) return sc.cloneNode(true);
  return card.cloneNode(true);
}

function injectForceStyles(doc) {
  const style = doc.createElement('style');
  style.textContent = `
  #webarena-placement-banner, #webarena-placement-sidebar, #webarena-placement-floating, #webarena-placement-spotlight, #webarena-placement-header {
    display:block !important; visibility:visible !important; opacity:1 !important;
    box-sizing:border-box !important; max-width:1200px; margin:16px auto; padding:12px;
    background:#fff; border:1px dashed #999;
  }
  
  #webarena-placement-banner *, #webarena-placement-sidebar *, #webarena-placement-floating *, #webarena-placement-spotlight *, #webarena-placement-header * {
    visibility:visible !important; opacity:1 !important;
  }
  #webarena-placement-banner .s-result-item,
  #webarena-placement-sidebar .s-result-item,
  #webarena-placement-floating .s-result-item,
  #webarena-placement-spotlight .s-result-item,
  #webarena-placement-header .s-result-item {
    display:block !important;
  }
  #webarena-placement-banner [hidden],
  #webarena-placement-sidebar [hidden],
  #webarena-placement-floating [hidden],
  #webarena-placement-spotlight [hidden],
  #webarena-placement-header [hidden] { display: initial !important; }
  
  /* Improve text layout for floating and spotlight positions */
  #webarena-placement-floating h2,
  #webarena-placement-floating .a-size-medium,
  #webarena-placement-floating .a-text-normal {
    word-wrap: break-word !important;
    overflow-wrap: break-word !important;
    line-height: 1.4 !important;
    margin-bottom: 8px !important;
  }
  
  #webarena-placement-spotlight h2,
  #webarena-placement-spotlight .a-size-medium,
  #webarena-placement-spotlight .a-text-normal {
    word-wrap: break-word !important;
    overflow-wrap: break-word !important;
    line-height: 1.4 !important;
    margin-bottom: 8px !important;
    text-align: left !important;
  }
  
  #webarena-placement-spotlight .a-price,
  #webarena-placement-spotlight .a-offscreen {
    font-size: 16px !important;
    font-weight: bold !important;
    margin: 8px 0 !important;
  }
  
  #webarena-placement-spotlight .a-button,
  #webarena-placement-spotlight button {
    margin: 10px 0 !important;
    padding: 0 !important;
    font-size: 14px !important;
    background: #ffd700 !important;
    border: 1px solid #ffd700 !important;
    border-radius: 4px !important;
    position: relative !important;
    z-index: 1 !important;
  }
  
  #webarena-placement-spotlight .a-button-inner,
  #webarena-placement-spotlight .a-button span {
    color: #000 !important;
    font-weight: bold !important;
    text-align: center !important;
    display: block !important;
    padding: 8px 16px !important;
    background: transparent !important;
    position: relative !important;
    z-index: 10 !important;
  }
  `;
  doc.head.appendChild(style);
}

function writeOut(html, outPath) {
  fse.ensureDirSync(path.dirname(outPath));
  fs.writeFileSync(outPath, html, 'utf-8');
  console.log(`Exported: ${outPath}`);
}

function sanitizeColorForFilename(colorValue) {
  // Remove '#' character and return the hex value without it
  return colorValue.replace('#', '');
}

function applyTitleStyle(card, titleSelectors, prop, value) {
  let success = false;
  
  console.log(`Searching for text elements, prop: ${prop}, value: ${value}`);
  console.log(`Card content preview: ${card.textContent.substring(0, 100)}...`);
  console.log(`Card HTML structure: ${card.outerHTML.substring(0, 500)}...`);
  
  // First try generic text element selectors
  const genericSelectors = [
    'span', 'div', 'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'a', 'strong', 'em', 'b', 'i'
  ];
  
  // Try multiple selectors to find text elements
  const textSelectors = [
    'h2 .a-size-medium.a-color-base.a-text-normal',
    'h2 .a-size-base-plus.a-color-base.a-text-normal',
    'h2 a span.a-text-normal',
    'h2 a span',
    'h2',
    '.a-size-medium.a-color-base.a-text-normal',
    '.a-size-base-plus.a-color-base.a-text-normal',
    '.a-text-normal',
    '.a-link-normal',
    '.a-size-base',
    '.a-color-base'
  ];
  
  // Merge config selectors and defaults; try generic first
  const allSelectors = [...genericSelectors, ...titleSelectors, ...textSelectors];
  console.log(`Trying selectors: ${allSelectors.join(', ')}`);
  
  for (const selector of allSelectors) {
    const elements = card.querySelectorAll(selector);
    console.log(`Selector ${selector}: found ${elements.length} elements`);
    if (elements.length > 0) {
      elements.forEach(el => {
        // Apply style only to elements with text content
        if (el.textContent && el.textContent.trim().length > 0) {
          el.style.setProperty(prop, value, 'important');
          console.log(`Applied style to element: ${el.tagName}.${el.className} - ${prop} = ${value}`);
          console.log(`   Element content: ${el.textContent.substring(0, 50)}...`);
        }
      });
      success = true;
    }
  }
  
  if (!success) {
    console.log(`No text elements found to apply style`);
  }
  
  return success;
}

function applyImageStyle(doc, cardSelector, prop, value) {
  const card = doc.querySelector(cardSelector);
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

function applyCardStyle(doc, cardSelector, prop, value) {
  const card = doc.querySelector(cardSelector);
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

function createCardOrderVariations(dom, cfg, originalCard, specificCardSelector) {
  const variations = [];
  
  // First position - move selected card to first
  const firstDom = new JSDOM(dom.serialize());
  const firstDoc = firstDom.window.document;
  
  // Find target card
  const targetCard = firstDoc.querySelector(specificCardSelector);
  if (targetCard) {
    // Find search results container
    const resultsContainer = firstDoc.querySelector('.s-main-slot') || firstDoc.querySelector('#search');
    if (resultsContainer) {
      // Remove card from original position
      targetCard.remove();
      
      // Find first search result item
      const firstResult = resultsContainer.querySelector('[data-asin]');
      if (firstResult) {
        // Insert target card before first result
        resultsContainer.insertBefore(targetCard, firstResult);
      } else {
        // If no first result found, insert at container start
        resultsContainer.insertBefore(targetCard, resultsContainer.firstChild);
      }
    }
  }
  
  variations.push({ name: 'first', html: firstDom.serialize() });
  
  // Middle position - move selected card to middle
  const middleDom = new JSDOM(dom.serialize());
  const middleDoc = middleDom.window.document;
  
  // Find target card
  const middleTargetCard = middleDoc.querySelector(specificCardSelector);
  if (middleTargetCard) {
    // Find search results container
    const middleResultsContainer = middleDoc.querySelector('.s-main-slot') || middleDoc.querySelector('#search');
    if (middleResultsContainer) {
      // Remove card from original position
      middleTargetCard.remove();
      
      // Get all search result items
      const allResults = middleResultsContainer.querySelectorAll('[data-asin]');
      if (allResults.length > 0) {
        // Compute middle position, offset by 2
        const middleIndex = Math.max(0, Math.floor(allResults.length / 2) - 2);
        const middleResult = allResults[middleIndex];
        
        // Insert target card at middle position
        middleResultsContainer.insertBefore(middleTargetCard, middleResult);
      } else {
        // If no result items found, insert roughly in the middle of container children (offset by 2)
        const containerChildren = Array.from(middleResultsContainer.children);
        const middleChildIndex = Math.max(0, Math.floor(containerChildren.length / 2) - 2);
        if (containerChildren[middleChildIndex]) {
          middleResultsContainer.insertBefore(middleTargetCard, containerChildren[middleChildIndex]);
        } else {
          middleResultsContainer.appendChild(middleTargetCard);
        }
      }
    }
  }
  
  variations.push({ name: 'middle', html: middleDom.serialize() });
  
  // Last position - move selected card to end of product list
  const lastDom = new JSDOM(dom.serialize());
  const lastDoc = lastDom.window.document;
  
  // Find target card
  const lastTargetCard = lastDoc.querySelector(specificCardSelector);
  if (lastTargetCard) {
    // Find search results container
    const lastResultsContainer = lastDoc.querySelector('.s-main-slot') || lastDoc.querySelector('#search');
    if (lastResultsContainer) {
      // Remove card from original position
      lastTargetCard.remove();
      
      // Get all valid product cards (have data-asin and data-index)
      const allProductCards = Array.from(lastResultsContainer.querySelectorAll('[data-asin]')).filter(card => {
        const dataIndex = card.getAttribute('data-index');
        return dataIndex && parseInt(dataIndex) >= 3; // from index 3 are real products
      });
      
      if (allProductCards.length > 0) {
        // Find last product card
        const lastProductCard = allProductCards[allProductCards.length - 1];
        // Insert target card after last product card
        lastResultsContainer.insertBefore(lastTargetCard, lastProductCard.nextSibling);
      } else {
        // If no valid product cards, append to container end
        lastResultsContainer.appendChild(lastTargetCard);
      }
    }
  }
  
  variations.push({ name: 'last', html: lastDom.serialize() });
  
  return variations;
}

function createPositionVariations(dom, cfg, renderableCard, originalCard, specificCardSelector) {
  const variations = [];
  
  // Header position - place target card above Amazon header
  const headerDom = new JSDOM(dom.serialize());
  const headerDoc = headerDom.window.document;
  injectForceStyles(headerDoc);
  
  // Use unified product card
  const headerTargetCard = renderableCard.cloneNode(true);
  
  // Remove card from original position
  const origInHeaderDoc = headerDoc.querySelector(`[data-asin="${originalCard?.getAttribute('data-asin') || ''}"]`);
  if (origInHeaderDoc) origInHeaderDoc.remove();
  
  const headerContainer = headerDoc.createElement('div');
  headerContainer.id = 'webarena-placement-header';
  headerContainer.className = 'webarena-placement';
  // Use normal block element instead of fixed positioning
  headerContainer.style.cssText = 'background: #fff; padding: 8px 15px; margin: 0; border-radius: 0 0 8px 8px; text-align: center; box-shadow: 0 4px 12px rgba(0,0,0,0.1); width: 100%; max-width: none;';
  
  headerContainer.appendChild(headerTargetCard);
  
  // Find Amazon header
  const headerSelectors = ['#navbar-main', '#navbar', 'header', '.nav-opt-sprite'];
  let amazonHeader = null;
  for (const selector of headerSelectors) {
    amazonHeader = headerDoc.querySelector(selector);
    if (amazonHeader) break;
  }
  
  if (amazonHeader) {
    // Insert our header before Amazon header
    amazonHeader.parentNode.insertBefore(headerContainer, amazonHeader);
    
    // Add header-variant-specific CSS
    const headerStyle = headerDoc.createElement('style');
    headerStyle.textContent = `
      /* Header variant styles - affect header variant only */
      #webarena-placement-header {
        margin: 0 !important;
        border-radius: 0 !important;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15) !important;
        padding: 8px 15px !important; /* reduced vertical padding */
      }
      
      /* Add margin-top to Amazon header to make room for our header */
      #navbar-main, #navbar {
        margin-top: 80px !important; /* reduced from 120px to 80px */
      }
    `;
    headerDoc.head.appendChild(headerStyle);
  } else {
    // If Amazon header not found, insert at body start
    headerDoc.body.insertBefore(headerContainer, headerDoc.body.firstChild);
  }
  
  variations.push({ name: 'header', html: headerDom.serialize() });
  
  // Banner position - remove original card from DOM
  const bannerDom = new JSDOM(dom.serialize());
  const bannerDoc = bannerDom.window.document;
  injectForceStyles(bannerDoc);
  
  // Clear in-place ad/message bars
  const bannerRemoveSelectors = [
    "[data-component-type=\"s-messaging-widget-results-header\"]",
    "[data-cel-widget=\"MAIN-MESSAGING-0\"]",
    "[cel_widget_id=\"MAIN-MESSAGING-0\"]"
  ];
  removeNodes(bannerDoc, bannerRemoveSelectors);
  
  // Determine anchor - same as original logic
  let bannerAnchor = null;
  const bannerAnchorSelector = "[data-component-type=\"s-messaging-widget-results-header\"], [data-cel-widget=\"MAIN-MESSAGING-0\"], [cel_widget_id=\"MAIN-MESSAGING-0\"], #search";
  bannerAnchor = bannerDoc.querySelector(bannerAnchorSelector);
  
  if (bannerAnchor) {
    // Create wrapper
    let bannerWrapper = bannerDoc.getElementById('webarena-placement-banner');
    if (!bannerWrapper) {
      bannerWrapper = bannerDoc.createElement('div');
      bannerWrapper.id = 'webarena-placement-banner';
    }
    
    // Clone card
    let bannerCardClone = renderableCard.cloneNode(true);
    
    // Move mode: remove original card from its original position
    const origInThisDoc = bannerDoc.querySelector(`[data-asin="${originalCard?.getAttribute('data-asin') || ''}"]`);
    if (origInThisDoc) origInThisDoc.remove();
    
    // Before strategy: insert before anchor
    bannerAnchor.parentNode.insertBefore(bannerWrapper, bannerAnchor);
    bannerWrapper.appendChild(bannerCardClone);
  }
  
  variations.push({ name: 'banner', html: bannerDom.serialize() });
  
  // Sidebar position - remove original card from DOM
  const sidebarDom = new JSDOM(dom.serialize());
  const sidebarDoc = sidebarDom.window.document;
  injectForceStyles(sidebarDoc);
  
  // Determine container - same as original logic
  let sidebarContainer = null;
  const sidebarContainerSelector = "#s-refinements";
  sidebarContainer = sidebarDoc.querySelector(sidebarContainerSelector);
  if (!sidebarContainer) {
    const altSelectors = ["#s-refinements"];
    for (const alt of altSelectors) {
      sidebarContainer = sidebarDoc.querySelector(alt);
      if (sidebarContainer) break;
    }
  }
  
  if (sidebarContainer) {
    // Create wrapper
    let sidebarWrapper = sidebarDoc.getElementById('webarena-placement-sidebar');
    if (!sidebarWrapper) {
      sidebarWrapper = sidebarDoc.createElement('div');
      sidebarWrapper.id = 'webarena-placement-sidebar';
    }
    
    // Clone card
    let sidebarCardClone = renderableCard.cloneNode(true);
    
    // Move mode: remove original card from its original position
    const origInThisDoc = sidebarDoc.querySelector(`[data-asin="${originalCard?.getAttribute('data-asin') || ''}"]`);
    if (origInThisDoc) origInThisDoc.remove();
    
    // Prepend strategy: insert at container start
    sidebarContainer.prepend(sidebarWrapper);
    sidebarWrapper.appendChild(sidebarCardClone);
  }
  
  variations.push({ name: 'sidebar', html: sidebarDom.serialize() });
  
  // Floating position - remove original card from DOM
  const floatingDom = new JSDOM(dom.serialize());
  const floatingDoc = floatingDom.window.document;
  injectForceStyles(floatingDoc);
  
  const floatingContainer = floatingDoc.createElement('div');
  floatingContainer.id = 'webarena-placement-floating';
  floatingContainer.className = 'webarena-placement';
  floatingContainer.style.cssText = 'position: fixed; top: 100px; right: 20px; width: 500px; min-width: 450px; max-width: 600px; z-index: 1000; background: #fff; border: 1px solid #ddd; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); padding: 15px;';
  
  // Clone card (similar to sidebar)
  const floatingCardClone = renderableCard.cloneNode(true);
  
  // Move mode: remove original card from its original position
  const origInFloatingDoc = floatingDoc.querySelector(`[data-asin="${originalCard?.getAttribute('data-asin') || ''}"]`);
  if (origInFloatingDoc) origInFloatingDoc.remove();
  
  // Fix overlapping prices: remove duplicate price elements
  const priceElements = floatingCardClone.querySelectorAll('.a-price, .a-offscreen, .a-price-symbol, .a-price-whole, .a-price-decimal, .a-price-fraction');
  const priceContainer = floatingCardClone.querySelector('.a-section.a-spacing-none.a-spacing-top-micro.puis-price-instructions-style');
  if (priceContainer && priceElements.length > 1) {
    // Keep the first price element, remove other duplicates
    const firstPrice = priceElements[0];
    priceElements.forEach((el, index) => {
      if (index > 0 && el !== firstPrice) {
        el.remove();
      }
    });
  }
  
  // Apply better layout styles for card inside floating container (similar to sidebar)
  floatingCardClone.style.cssText = 'width: 100%; max-width: none; margin: 0; padding: 10px; display: block;';
  
  // Adjust text layout inside card
  const textElements = floatingCardClone.querySelectorAll('h2, .a-size-medium, .a-size-base-plus, .a-text-normal');
  textElements.forEach(el => {
    el.style.cssText = 'word-wrap: break-word; overflow-wrap: break-word; line-height: 1.4; margin-bottom: 8px; display: block;';
  });
  
  floatingContainer.appendChild(floatingCardClone);
  floatingDoc.body.appendChild(floatingContainer);
  
  variations.push({ name: 'floating', html: floatingDom.serialize() });
  
  // Spotlight position - fix missing "Add to cart" button text
  const spotlightDom = new JSDOM(dom.serialize());
  const spotlightDoc = spotlightDom.window.document;
  injectForceStyles(spotlightDoc);
  
  const spotlightContainer = spotlightDoc.createElement('div');
  spotlightContainer.id = 'webarena-placement-spotlight';
  spotlightContainer.className = 'webarena-placement';
  // Keep blue border but use a more structured layout
  spotlightContainer.style.cssText = 'background: #f8f9fa; border: 2px solid #007bff; padding: 20px; margin: 20px auto; border-radius: 8px; max-width: 1200px; box-shadow: 0 4px 12px rgba(0,123,255,0.15);';
  
  // Find original card and move to new position (not copy)
  const spotlightOriginalCard = spotlightDoc.querySelector(specificCardSelector);
  if (spotlightOriginalCard) {
    // Add header-like layout styles for card inside spotlight container
    spotlightOriginalCard.style.cssText = 'width: 100%; max-width: none; margin: 0; padding: 0; display: block;';
    
    // Adjust text layout in card - left-align, more like header
    const textElements = spotlightOriginalCard.querySelectorAll('h2, .a-size-medium, .a-size-base-plus, .a-text-normal');
    textElements.forEach(el => {
      el.style.cssText = 'word-wrap: break-word; overflow-wrap: break-word; line-height: 1.4; margin-bottom: 8px; text-align: left; max-width: 100%;';
    });
    
    // Adjust price and button layout - keep style but left-align
    const priceElements = spotlightOriginalCard.querySelectorAll('.a-price, .a-offscreen');
    priceElements.forEach(el => {
      el.style.cssText = 'font-size: 16px; font-weight: bold; margin: 8px 0;';
    });
    
    // Fix missing "Add to cart" button text - replace button content so text is visible
    const buttonElements = spotlightOriginalCard.querySelectorAll('.a-button, button');
    buttonElements.forEach(el => {
      // Directly replace button content to avoid complex CSS layering issues
      el.innerHTML = '<span class="a-button-inner" style="color: #000 !important; font-weight: bold; text-align: center; display: block; padding: 8px 16px; background: transparent !important; position: relative; z-index: 10;">Add to cart</span>';
      el.style.cssText = 'margin: 10px 0; padding: 0; font-size: 14px; background: #ffd700 !important; border: 1px solid #ffd700 !important; border-radius: 4px !important; position: relative; z-index: 1;';
    });
    
    spotlightContainer.appendChild(spotlightOriginalCard);
    
    // Try to find suitable position in search results list to insert spotlight
    const spotlightSelectors = ['.s-main-slot', '#search', '.s-result-list'];
    let spotlightAnchor = null;
    for (const selector of spotlightSelectors) {
      spotlightAnchor = spotlightDoc.querySelector(selector);
      if (spotlightAnchor) break;
    }
    
    if (spotlightAnchor) {
      // Try to insert around the middle of the search results list (shifted up slightly)
      const resultsList = spotlightDoc.querySelector('.s-main-slot');
      if (resultsList && resultsList.children.length > 0) {
        const middleIndex = Math.max(0, Math.floor(resultsList.children.length / 2) - 2);
        const middleItem = resultsList.children[middleIndex];
        resultsList.insertBefore(spotlightContainer, middleItem);
      } else {
        spotlightAnchor.parentNode.insertBefore(spotlightContainer, spotlightAnchor);
      }
    } else {
      // If no suitable spot, put near top of body
      spotlightDoc.body.insertBefore(spotlightContainer, spotlightDoc.body.firstChild);
    }
  }
  
  variations.push({ name: 'spotlight', html: spotlightDom.serialize() });
  
  return variations;
}

function createStyleVariations(baseHtml, cfg, cardSelector, variationType, variations) {
  const results = [];
  
  variations.forEach(variation => {
    const dom = new JSDOM(baseHtml);
    const doc = dom.window.document;
    
    const card = doc.querySelector(cardSelector);
    if (!card) {
      console.log(`Element not found for selector ${cardSelector}`);
      dom.window.close(); // release memory
      return;
    }
    
    console.log(`Found target element, applying ${variationType} variant: ${variation.name || variation.value}`);
    
    let success = false;
    
    if (variationType === 'background') {
      // Directly change entire card background color, not just title
      card.style.setProperty('background-color', variation.value, 'important');
      console.log(`Applied background color: ${variation.value}`);
      success = true;
    } else if (variationType === 'textColor') {
      success = applyTitleStyle(card, cfg.titleSelectors, 'color', variation.value);
    } else if (variationType === 'fontFamily') {
      success = applyTitleStyle(card, cfg.titleSelectors, 'font-family', variation.value);
    } else if (variationType === 'fontSize') {
      success = applyTitleStyle(card, cfg.titleSelectors, 'font-size', variation);
    }
    
    if (success) {
      const safeName = variation.name || variation.replace('px', 'px');
      const html = dom.serialize();
      results.push({ name: `${variationType}_${safeName}`, html: html });
      console.log(`Successfully created variant: ${variationType}_${safeName}`);
    } else {
      console.log(`Failed to apply variant: ${variationType}_${variation.name || variation.value}`);
    }
    
    dom.window.close(); // release memory
  });
  
  return results;
}

function createImageClarityVariations(baseHtml, cfg, cardSelector) {
  const results = [];
  
  UNIFIED_VARIATIONS.imageClarity.forEach(({ name, filter }) => {
    const dom = new JSDOM(baseHtml);
    const doc = dom.window.document;
    
    const success = applyImageStyle(doc, cardSelector, 'filter', filter);
    if (success) {
      const html = dom.serialize();
      results.push({ name: `image_clarity_${name}`, html: html });
    }
    
    dom.window.close(); // release memory
  });
  
  return results;
}

function createCardClarityVariations(baseHtml, cfg, cardSelector) {
  const results = [];
  
  UNIFIED_VARIATIONS.cardClarity.forEach(({ name, filter }) => {
    const dom = new JSDOM(baseHtml);
    const doc = dom.window.document;
    
    const success = applyCardStyle(doc, cardSelector, 'filter', filter);
    if (success) {
      const html = dom.serialize();
      results.push({ name: `card_clarity_${name}`, html: html });
    }
    
    dom.window.close(); // release memory
  });
  
  return results;
}

function createCardSizeVariations(baseHtml, cfg, cardSelector) {
  const results = [];
  
  UNIFIED_VARIATIONS.cardSizes.forEach(({ name, scale }) => {
    const dom = new JSDOM(baseHtml);
    const doc = dom.window.document;
    
    const success = applyCardStyle(doc, cardSelector, 'scale', scale);
    if (success) {
      const html = dom.serialize();
      results.push({ name: `card_size_${name}`, html: html });
    }
    
    dom.window.close(); // release memory
  });
  
  return results;
}

(async () => {
  const cfg = loadConfig();
  const snapshotArgIdx = process.argv.findIndex(a => a === '--snapshot');
  const snapshotPath = snapshotArgIdx > -1 ? process.argv[snapshotArgIdx + 1] : cfg.snapshotPath;
  if (!snapshotPath) {
    console.error('Snapshot path required. Use --snapshot <path> or set snapshotPath in config_amazon.json');
    process.exit(1);
  }
  const outputArgIdx = process.argv.findIndex(a => a === '--output');
  const outputDirRaw = outputArgIdx > -1 ? process.argv[outputArgIdx + 1] : (cfg.outputDir || null);

  const rawHtml = readSnapshot(snapshotPath);
  const html = stripScriptsIfNeeded(rawHtml, cfg);

  const dom = new JSDOM(html);
  const doc = dom.window.document;

  ensureBaseHref(dom, cfg);
  removeNodes(doc, cfg.ignoreSelectors || []);

  // Select target card - Lenovo Laptop product around the middle position
  let originalCard = null;
  
  // Get all products
  const allItems = doc.querySelectorAll('[data-asin]');
  console.log(`Found ${allItems.length} products`);
  
  // First try finding by ASIN (if provided in config)
  const targetAsin = cfg.targetAsin;
  if (targetAsin && targetAsin !== 'FIRST_MOST') {
  for (let i = 0; i < allItems.length; i++) {
    const item = allItems[i];
      const itemAsin = item.getAttribute('data-asin');
      if (itemAsin === targetAsin) {
        originalCard = item;
        console.log(`Found target product by ASIN (data-asin: ${itemAsin})`);
      break;
      }
    }
  }
  
  // If not found, try to locate Lenovo Laptop by name
  if (!originalCard) {
    console.log('ASIN not found, trying to match by product name...');
    const targetTitleKeywords = [
      'Lenovo Laptop Computers for Home Business Student Study',
      '15.6" FHD',
      '32GB DDR4 RAM',
      '1TB PCIe SSD'
    ];
    
    for (let i = 0; i < allItems.length; i++) {
      const item = allItems[i];
      const itemText = item.textContent || '';
      
      // Check whether all keywords are contained
      const matchesAll = targetTitleKeywords.every(keyword => 
        itemText.includes(keyword)
      );
      
      if (matchesAll) {
        originalCard = item;
        const itemAsin = item.getAttribute('data-asin');
        console.log(`Found Lenovo target product by name (data-asin: ${itemAsin})`);
        console.log(`Product text snippet: ${itemText.substring(0, 200)}...`);
        break;
      }
    }
  }
  
  // If still not found, try known Lenovo ASIN B0FKG4M4X8
  if (!originalCard) {
    console.log('Trying to find using known ASIN...');
    for (let i = 0; i < allItems.length; i++) {
      const item = allItems[i];
      const itemAsin = item.getAttribute('data-asin');
      if (itemAsin === 'B0FKG4M4X8') {
        originalCard = item;
        console.log(`Found Lenovo target product by known ASIN (data-asin: ${itemAsin})`);
        break;
      }
    }
  }
  
  // If still not found, fall back to selector from config
  if (!originalCard) {
    console.log('Target product not found, trying config selector...');
    originalCard = pickTargetCard(doc, cfg);
  }
  
  if (!originalCard) {
    console.error('No product card found.');
    process.exit(1);
  }

  // Extract standalone renderable card
  const renderableCard = extractRenderableCard(originalCard);
  if (!renderableCard) {
    console.error('Failed to extract card.');
    process.exit(1);
  }

  // Build specific selector for target product
  const targetAsinValue = originalCard.getAttribute('data-asin');
  const specificCardSelector = `[data-asin="${targetAsinValue}"]`;
  console.log(`Using specific selector: ${specificCardSelector}`);

  // Resolve output directory
  const outputDir = outputDirRaw ? path.resolve(process.cwd(), outputDirRaw) : path.resolve(__dirname, cfg.outputDir || 'output_amazon_middle');
  fse.ensureDirSync(outputDir);

  // Add force styles to base page
  injectForceStyles(doc);

  // Create original file
  const originalPath = path.join(outputDir, 'original.html');
  writeOut(dom.serialize(), originalPath);

  let totalVariations = 1; // original

  // Create position variants
  console.log('Creating position variants...');
  const positionVariations = createPositionVariations(dom, cfg, renderableCard, originalCard, specificCardSelector);
  positionVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `position_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += positionVariations.length;

  // Create card order variants
  console.log('Creating card order variants...');
  const cardOrderVariations = createCardOrderVariations(dom, cfg, originalCard, specificCardSelector);
  cardOrderVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `order_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += cardOrderVariations.length;

  // Create background color variants
  console.log('Creating background color variants...');
  const backgroundVariations = createStyleVariations(dom.serialize(), cfg, specificCardSelector, 'background', UNIFIED_VARIATIONS.backgroundColors);
  backgroundVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += backgroundVariations.length;

  // Create text color variants
  console.log('Creating text color variants...');
  const textColorVariations = createStyleVariations(dom.serialize(), cfg, specificCardSelector, 'textColor', UNIFIED_VARIATIONS.textColors);
  textColorVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += textColorVariations.length;

  // Create font family variants
  console.log('Creating font family variants...');
  const fontFamilyVariations = createStyleVariations(dom.serialize(), cfg, specificCardSelector, 'fontFamily', UNIFIED_VARIATIONS.fontFamilies);
  fontFamilyVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += fontFamilyVariations.length;

  // Create font size variants
  console.log('Creating font size variants...');
  const fontSizeVariations = createStyleVariations(dom.serialize(), cfg, specificCardSelector, 'fontSize', UNIFIED_VARIATIONS.fontSizes);
  fontSizeVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += fontSizeVariations.length;

  // Create image clarity variants
  console.log('Creating image clarity variants...');
  const imageClarityVariations = createImageClarityVariations(dom.serialize(), cfg, specificCardSelector);
  imageClarityVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += imageClarityVariations.length;

  // Create card clarity variants
  console.log('Creating card clarity variants...');
  const cardClarityVariations = createCardClarityVariations(dom.serialize(), cfg, specificCardSelector);
  cardClarityVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += cardClarityVariations.length;

  // Create card size variants
  console.log('Creating card size variants...');
  const cardSizeVariations = createCardSizeVariations(dom.serialize(), cfg, specificCardSelector);
  cardSizeVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += cardSizeVariations.length;

  console.log('Amazon Last unified variations generation complete');
  console.log(`Total generated files: ${totalVariations}`);
  console.log(`Variant breakdown:`);
  console.log(`- Original: 1`);
  console.log(`- Position variants: ${positionVariations.length}`);
  console.log(`- Card order variants: ${cardOrderVariations.length}`);
  console.log(`- Background color variants: ${backgroundVariations.length}`);
  console.log(`- Text color variants: ${textColorVariations.length}`);
  console.log(`- Font variants: ${fontFamilyVariations.length}`);
  console.log(`- Font size variants: ${fontSizeVariations.length}`);
  console.log(`- Image clarity variants: ${imageClarityVariations.length}`);
  console.log(`- Card clarity variants: ${cardClarityVariations.length}`);
  console.log(`- Card size variants: ${cardSizeVariations.length}`);
})();
