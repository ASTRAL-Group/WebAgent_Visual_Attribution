const fs = require('fs');
const fse = require('fs-extra');
const path = require('path');
const { JSDOM } = require('jsdom');

// Unified variation config (amazon_second: only a few variants)
const UNIFIED_VARIATIONS = {
  // Background color variants (2)
  backgroundColors: [
    { name: '2196f3', value: '#2196f3' },
    { name: '4caf50', value: '#4caf50' }
  ],
  // Text color variants (1) - use normal color
  textColors: [
    { name: '0d6efd', value: '#0d6efd' }
  ],
  // Font variants (1)
  fontFamilies: [
    { name: 'arial', value: 'Arial, sans-serif' }
  ],
  // Image clarity variants (1) - use blur_2px
  imageClarity: [
    { name: 'blur_2px', filter: 'blur(2px)' }
  ],
  // Card clarity variants (1) - use blur_2px
  cardClarity: [
    { name: 'blur_2px', filter: 'blur(2px)' }
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
  #webarena-placement-banner, #webarena-placement-sidebar, #webarena-placement-spotlight, #webarena-placement-header {
    display:block !important; visibility:visible !important; opacity:1 !important;
    box-sizing:border-box !important; max-width:1200px; margin:16px auto; padding:12px;
    background:#fff; border:1px dashed #999;
  }
  
  #webarena-placement-banner *, #webarena-placement-sidebar *, #webarena-placement-spotlight *, #webarena-placement-header * {
    visibility:visible !important; opacity:1 !important;
  }
  #webarena-placement-banner .s-result-item,
  #webarena-placement-sidebar .s-result-item,
  #webarena-placement-spotlight .s-result-item,
  #webarena-placement-header .s-result-item {
    display:block !important;
  }
  #webarena-placement-banner [hidden],
  #webarena-placement-sidebar [hidden],
  #webarena-placement-spotlight [hidden],
  #webarena-placement-header [hidden] { display: initial !important; }
  
  /* Improve text layout for spotlight position */
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
  
  // Price-related class names; exclude these elements to avoid breaking price layout
  const priceRelatedClasses = [
    'a-price', 'a-price-symbol', 'a-price-whole', 'a-price-fraction', 
    'a-price-decimal', 'a-offscreen', 'a-price-instructions-style',
    'puis-price-instructions-style'
  ];
  
  // Check if element is price-related or a descendant
  function isPriceRelatedElement(el) {
    // Check element's own class name
    const className = el.className || '';
    if (typeof className === 'string') {
      for (const priceClass of priceRelatedClasses) {
        if (className.includes(priceClass)) {
          return true;
        }
      }
    } else if (className && className.baseVal) {
      // SVG element case
      const classList = className.baseVal || '';
      for (const priceClass of priceRelatedClasses) {
        if (classList.includes(priceClass)) {
          return true;
        }
      }
    }
    
    // Check if descendant of price-related element
    let parent = el.parentElement;
    let depth = 0;
    while (parent && depth < 5) {
      const parentClassName = parent.className || '';
      if (typeof parentClassName === 'string') {
        for (const priceClass of priceRelatedClasses) {
          if (parentClassName.includes(priceClass)) {
            return true;
          }
        }
      }
      parent = parent.parentElement;
      depth++;
    }
    
    return false;
  }
  
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
        // Exclude price-related elements; apply style only to elements with text content
        if (el.textContent && el.textContent.trim().length > 0 && !isPriceRelatedElement(el)) {
          el.style.setProperty(prop, value, 'important');
          console.log(`Applied style to element: ${el.tagName}.${el.className} - ${prop} = ${value}`);
          console.log(`   Element content: ${el.textContent.substring(0, 50)}...`);
        } else if (isPriceRelatedElement(el)) {
          console.log(`Skipping price-related element: ${el.tagName}.${el.className}`);
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
        // If no first result found, append to container start
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
        // If no result items found, insert at container middle, offset by 2
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
  
  // Last position - move selected card to end of product list (before Related searches etc.)
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
      
      // Get all valid product cards (have data-asin, non-empty, not widgets)
      const allProductCards = Array.from(lastResultsContainer.querySelectorAll('[data-asin]')).filter(card => {
        const asin = card.getAttribute('data-asin');
        const dataIndex = card.getAttribute('data-index');
        
        // Filter out empty-asin widgets (e.g. "Picks from Amazon Influencers", "Related searches")
        if (!asin || asin.trim() === '') {
          return false;
        }
        
        // Filter out non-product widgets (by cel_widget_id)
        const widgetId = card.getAttribute('cel_widget_id') || card.querySelector('[cel_widget_id]')?.getAttribute('cel_widget_id');
        if (widgetId && (widgetId.includes('FEATURED_ASINS') || widgetId.includes('TEXT_REFORMULATION'))) {
          return false;
        }
        
        // Filter out elements containing "Picks from Amazon Influencers" or "Related searches"
        const cardText = card.textContent || '';
        if (cardText.includes('Picks from Amazon Influencers') || cardText.includes('Related searches')) {
          return false;
        }
        
        return true;
      });
      
      if (allProductCards.length > 0) {
        // Find last real product card
        const lastProductCard = allProductCards[allProductCards.length - 1];
        
        // Find first non-product widget ("Picks from Amazon Influencers" or "Related searches")
        const nonProductSelectors = [
          '[data-asin=""][data-index="32"]', // Picks from Amazon Influencers
          '[data-asin=""][data-index="33"]', // Related searches
          '[cel_widget_id*="FEATURED_ASINS"]',
          '[cel_widget_id*="TEXT_REFORMULATION"]'
        ];
        
        let insertBefore = null;
        for (const selector of nonProductSelectors) {
          const nonProductElement = lastResultsContainer.querySelector(selector);
          if (nonProductElement) {
            insertBefore = nonProductElement;
            break;
          }
        }
        
        // If non-product widget found, insert before it; else insert after last product card
        if (insertBefore) {
          lastResultsContainer.insertBefore(lastTargetCard, insertBefore);
        } else {
          // Insert after last product card
          lastResultsContainer.insertBefore(lastTargetCard, lastProductCard.nextSibling);
        }
      } else {
        // If no valid product cards, try to find non-product widget and insert before it
        const nonProductSelectors = [
          '[data-asin=""][data-index="32"]',
          '[data-asin=""][data-index="33"]',
          '[cel_widget_id*="FEATURED_ASINS"]',
          '[cel_widget_id*="TEXT_REFORMULATION"]'
        ];
        
        let insertBefore = null;
        for (const selector of nonProductSelectors) {
          const nonProductElement = lastResultsContainer.querySelector(selector);
          if (nonProductElement) {
            insertBefore = nonProductElement;
            break;
          }
        }
        
        if (insertBefore) {
          lastResultsContainer.insertBefore(lastTargetCard, insertBefore);
        } else {
          // Fallback: append to container end
          lastResultsContainer.appendChild(lastTargetCard);
        }
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
  // Use normal block element, not fixed positioning
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
      /* Header variant styles - only affect header variant */
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
  
  // Clear in-place ad/message bar
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
    
    // Move mode: remove original card from original position
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
    
    // Move mode: remove original card from original position
    const origInThisDoc = sidebarDoc.querySelector(`[data-asin="${originalCard?.getAttribute('data-asin') || ''}"]`);
    if (origInThisDoc) origInThisDoc.remove();
    
    // Prepend strategy: insert at container start
    sidebarContainer.prepend(sidebarWrapper);
    sidebarWrapper.appendChild(sidebarCardClone);
  }
  
  variations.push({ name: 'sidebar', html: sidebarDom.serialize() });
  
  // Spotlight position - fix missing "Add to cart" button text
  const spotlightDom = new JSDOM(dom.serialize());
  const spotlightDoc = spotlightDom.window.document;
  injectForceStyles(spotlightDoc);
  
  const spotlightContainer = spotlightDoc.createElement('div');
  spotlightContainer.id = 'webarena-placement-spotlight';
  spotlightContainer.className = 'webarena-placement';
  // Keep blue border but use tidier layout
  spotlightContainer.style.cssText = 'background: #f8f9fa; border: 2px solid #007bff; padding: 20px; margin: 20px auto; border-radius: 8px; max-width: 1200px; box-shadow: 0 4px 12px rgba(0,123,255,0.15);';
  
  // Find original card and move to new position (not copy)
  const spotlightOriginalCard = spotlightDoc.querySelector(specificCardSelector);
  if (spotlightOriginalCard) {
    // Add header-like layout styles for card inside spotlight container
    spotlightOriginalCard.style.cssText = 'width: 100%; max-width: none; margin: 0; padding: 0; display: block;';
    
    // Adjust text element layout in card - left-align, more like header
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
      // Replace button content directly to avoid complex CSS stacking
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
      // Try to insert at middle of search results list, offset by 2
      const resultsList = spotlightDoc.querySelector('.s-main-slot');
      if (resultsList && resultsList.children.length > 0) {
        const middleIndex = Math.max(0, Math.floor(resultsList.children.length / 2) - 2);
        const middleItem = resultsList.children[middleIndex];
        resultsList.insertBefore(spotlightContainer, middleItem);
      } else {
        spotlightAnchor.parentNode.insertBefore(spotlightContainer, spotlightAnchor);
      }
    } else {
      // If no suitable position, place at body start
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
      // Modify entire card background color, not just title
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

  // Target: second product (HP 15.6" College Student 1TB). Strategy: targetAsinSecond, then 2nd card in DOM, then keyword match.
  let originalCard = null;
  const targetAsinSecond = cfg.targetAsinSecond;

  if (targetAsinSecond) {
    const byAsin = doc.querySelector(`div.s-result-item.s-asin[data-asin="${targetAsinSecond}"]`);
    if (byAsin) {
      originalCard = byAsin;
      console.log(`Target card found by targetAsinSecond (data-asin: ${targetAsinSecond})`);
    }
  }

  if (!originalCard) {
    const productCards = Array.from(doc.querySelectorAll('div.s-result-item.s-asin')).filter(el => {
      const asin = (el.getAttribute('data-asin') || '').trim();
      return asin.length > 0;
    });
    console.log(`Found ${productCards.length} product cards (DOM order)`);
    if (productCards.length > 1) {
      originalCard = productCards[1];
      console.log(`Using 2nd product (data-asin: ${originalCard.getAttribute('data-asin')})`);
    }
  }

  if (!originalCard) {
    const TARGET_KEYWORDS = ['College Student', '1TB PCIe SSD'];
    const allWithAsin = Array.from(doc.querySelectorAll('div.s-result-item.s-asin')).filter(el => (el.getAttribute('data-asin') || '').trim());
    for (const item of allWithAsin) {
      const text = item.textContent || '';
      if (TARGET_KEYWORDS.every(k => text.includes(k))) {
        originalCard = item;
        console.log(`Target found by keyword match (data-asin: ${originalCard.getAttribute('data-asin')})`);
        break;
      }
    }
  }

  if (!originalCard) {
    console.log('Second product not found, trying config selector...');
    originalCard = pickTargetCard(doc, cfg);
  }
  if (!originalCard) {
    console.error('No product card found.');
    process.exit(1);
  }
  const renderableCard = extractRenderableCard(originalCard);
  if (!renderableCard) {
    console.error('Failed to extract card.');
    process.exit(1);
  }
  const targetAsinValue = originalCard.getAttribute('data-asin');
  const specificCardSelector = `[data-asin="${targetAsinValue}"]`;
  console.log(`Using selector: ${specificCardSelector}`);

  const outputDir = outputDirRaw ? path.resolve(process.cwd(), outputDirRaw) : path.resolve(__dirname, 'output_amazon_second');
  fse.ensureDirSync(outputDir);

  // Add force styles to base page
  injectForceStyles(doc);

  // Create original file
  const originalPath = path.join(outputDir, 'original.html');
  writeOut(dom.serialize(), originalPath);

  let totalVariations = 1; // original

  console.log('Creating background color variations...');
  const backgroundVariations = createStyleVariations(dom.serialize(), cfg, specificCardSelector, 'background', UNIFIED_VARIATIONS.backgroundColors);
  backgroundVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += backgroundVariations.length;

  console.log('Creating text color variations...');
  const textColorVariations = createStyleVariations(dom.serialize(), cfg, specificCardSelector, 'textColor', UNIFIED_VARIATIONS.textColors);
  textColorVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += textColorVariations.length;

  console.log('Creating font variations...');
  const fontFamilyVariations = createStyleVariations(dom.serialize(), cfg, specificCardSelector, 'fontFamily', UNIFIED_VARIATIONS.fontFamilies);
  fontFamilyVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += fontFamilyVariations.length;

  console.log('Creating image clarity variations...');
  const imageClarityVariations = createImageClarityVariations(dom.serialize(), cfg, specificCardSelector);
  imageClarityVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += imageClarityVariations.length;

  console.log('Creating card clarity variations...');
  const cardClarityVariations = createCardClarityVariations(dom.serialize(), cfg, specificCardSelector);
  cardClarityVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += cardClarityVariations.length;

  console.log('Amazon Second variations done.');
  console.log(`Total: ${totalVariations} files (1 original + ${backgroundVariations.length} background + ${textColorVariations.length} textColor + ${fontFamilyVariations.length} font + ${imageClarityVariations.length} imageClarity + ${cardClarityVariations.length} cardClarity).`);
})();
