const fs = require('fs');
const fse = require('fs-extra');
const path = require('path');
const { JSDOM } = require('jsdom');

// Unified variation config
const UNIFIED_VARIATIONS = {
  // Position variants (banner from ebay, header/sidebar from ebay_position)
  positions: [
    { name: 'banner', description: 'Banner at top of page' },
    { name: 'header', description: 'Header at top of page' },
    { name: 'sidebar', description: 'Sidebar position, card scaled to fit sidebar width' }
  ],
  
  // Card order variants (3)
  cardOrders: [
    { name: 'first', description: 'Move selected card to first' },
    { name: 'middle', description: 'Move selected card to middle' },
    { name: 'last', description: 'Move selected card to last' }
  ],
  
  // Background color variants (12)
  backgroundColors: [
    { name: '2196f3', value: '#2196f3' },
    { name: '1976d2', value: '#1976d2' },
    { name: '42a5f5', value: '#42a5f5' },
    { name: '4caf50', value: '#4caf50' },
    { name: 'e91e63', value: '#e91e63' },
    { name: 'ffeb3b', value: '#ffeb3b' },
    { name: '6f42c1', value: '#6f42c1' },
    { name: 'ff9800', value: '#ff9800' },
    { name: '00bcd4', value: '#00bcd4' },
    { name: 'f44336', value: '#f44336' },
    { name: '9c27b0', value: '#9c27b0' },
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
  
  // Font size variants (6)
  fontSizes: ['14px', '16px', '18px', '20px', '22px', '24px'],
  
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

function readSnapshot(snapshotPath) {
  const html = fs.readFileSync(snapshotPath, 'utf-8');
  return html.replace(/<script[\s\S]*?<\/script>/gi, '');
}

function ensureBaseHref(dom) {
  const doc = dom.window.document;
  let base = doc.querySelector('base');
  if (!base) {
    base = doc.createElement('base');
    doc.head.appendChild(base);
  }
  base.setAttribute('href', 'https://www.ebay.com/');
}

function removeNodes(doc, selectors = []) {
  selectors.forEach(sel => {
    doc.querySelectorAll(sel).forEach(n => n.remove());
  });
}

function upgradeImageQuality(doc) {
  // Replace low-res image URLs with high-res
  const images = doc.querySelectorAll('img');
  images.forEach(img => {
    // Prefer high-res URL from data-defer-load
    const highResUrl = img.getAttribute('data-defer-load');
    if (highResUrl) {
      img.setAttribute('src', highResUrl);
      // Remove lazy-load attrs so image displays immediately
      img.removeAttribute('loading');
      img.removeAttribute('data-defer-load');
    } else {
      // If no data-defer-load, try replacing s-l140 with s-l500 in src
      const currentSrc = img.getAttribute('src');
      if (currentSrc) {
        const upgradedSrc = currentSrc.replace(/s-l140\.webp/g, 's-l500.webp')
                                      .replace(/s-l160\.webp/g, 's-l500.webp')
                                      .replace(/s-l225\.webp/g, 's-l500.webp')
                                      .replace(/s-l300\.webp/g, 's-l500.webp');
        if (upgradedSrc !== currentSrc) {
          img.setAttribute('src', upgradedSrc);
          img.removeAttribute('loading');
        }
      }
    }
  });
}

function extractRenderableCard(card) {
  if (!card) return null;
  // eBay item card structure: use full li.s-card so s-card__footer/separator is kept
  return card.cloneNode(true);
}

function injectForceStyles(doc) {
  const style = doc.createElement('style');
  style.textContent = `
  #webarena-placement-banner {
    display:block !important; visibility:visible !important; opacity:1 !important;
    box-sizing:border-box !important; max-width:1200px; margin:16px auto; padding:12px;
    background:#fff; border:1px dashed #999;
  }
  
  #webarena-placement-banner * {
    visibility:visible !important; opacity:1 !important;
  }
  #webarena-placement-banner .s-card,
  #webarena-placement-banner .s-item {
    display:block !important;
  }
  #webarena-placement-banner [hidden] { display: initial !important; }
  
  #webarena-placement-header {
    display:block !important; visibility:visible !important; opacity:1 !important;
    box-sizing:border-box !important; max-width:1200px; margin:16px auto; padding:12px;
    background:#fff; border:1px dashed #999;
  }
  #webarena-placement-header * { visibility:visible !important; opacity:1 !important; }
  #webarena-placement-header .s-card,
  #webarena-placement-header .s-item { display:block !important; }
  #webarena-placement-header [hidden] { display: initial !important; }
  
  #webarena-placement-sidebar {
    display:block !important; visibility:visible !important; opacity:1 !important;
    box-sizing:border-box !important; width:100% !important; max-width:100% !important;
    margin:0 0 16px 0 !important; padding:0 !important; background:transparent !important;
    position:relative !important; float:none !important; clear:both !important;
    overflow:visible !important;
  }
  #webarena-placement-sidebar + * { clear:both !important; }
  #webarena-placement-sidebar * { visibility:visible !important; opacity:1 !important; }
  #webarena-placement-sidebar .s-card,
  #webarena-placement-sidebar .s-item {
    display:flex !important; flex-direction:column !important; width:100% !important; max-width:100% !important;
    margin:0 !important; box-sizing:border-box !important;
  }
  #webarena-placement-sidebar .s-card > *,
  #webarena-placement-sidebar .s-item > * { width:100% !important; max-width:100% !important; }
  #webarena-placement-sidebar [hidden] { display: initial !important; }
  
  /* Spacing between item cards */
  ul.srp-results li.s-card {
    margin-bottom: 12px !important;
    padding-bottom: 8px !important;
  }
  
  /* Optional: light gray bottom border */
  ul.srp-results li.s-card:not(:last-child) {
    border-bottom: 1px solid #e5e5e5 !important;
    padding-bottom: 12px !important;
  }
  `;
  doc.head.appendChild(style);
}

function writeOut(html, outPath) {
  fse.ensureDirSync(path.dirname(outPath));
  fs.writeFileSync(outPath, html, 'utf-8');
  console.log(`Exported: ${outPath}`);
}

function applyTitleStyle(card, titleSelectors, prop, value) {
  let success = false;
  
  console.log(`Finding text elements, prop: ${prop}, value: ${value}`);
  
  // Price-related class names; exclude to avoid breaking price layout
  const priceRelatedClasses = [
    's-item__price', 's-item__price-display', 's-item__price-display__price',
    's-item__detail', 's-item__detail-section'
  ];
  
  function isPriceRelatedElement(el) {
    const className = el.className || '';
    if (typeof className === 'string') {
      for (const priceClass of priceRelatedClasses) {
        if (className.includes(priceClass)) {
          return true;
        }
      }
    }
    
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
  
  // eBay item title selectors
  const textSelectors = [
    '.s-card__title',
    '.s-card__title span',
    'div[role="heading"]',
    'div[role="heading"].s-card__title',
    '.su-styled-text',
    '.su-styled-text.primary',
    'h3',
    'div[aria-level="3"]'
  ];
  
  const allSelectors = [...titleSelectors, ...textSelectors];
  
  for (const selector of allSelectors) {
    const elements = card.querySelectorAll(selector);
    if (elements.length > 0) {
      elements.forEach(el => {
        if (el.textContent && el.textContent.trim().length > 0 && !isPriceRelatedElement(el)) {
          el.style.setProperty(prop, value, 'important');
          console.log(`Applied style to element: ${el.tagName}.${el.className} - ${prop} = ${value}`);
          success = true;
        }
      });
    }
  }
  
  if (!success) {
    console.log('No text elements found to apply style');
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

function createCardOrderVariations(dom, specificCardSelector) {
  const variations = [];
  
  // First position
  const firstDom = new JSDOM(dom.serialize());
  const firstDoc = firstDom.window.document;
  const targetCard = firstDoc.querySelector(specificCardSelector);
  if (targetCard) {
    const resultsContainer = firstDoc.querySelector('ul.srp-results');
    if (resultsContainer) {
      targetCard.remove();
      const firstResult = resultsContainer.querySelector('li.s-card');
      if (firstResult) {
        resultsContainer.insertBefore(targetCard, firstResult);
      } else {
        resultsContainer.insertBefore(targetCard, resultsContainer.firstChild);
      }
    }
  }
  variations.push({ name: 'first', html: firstDom.serialize() });
  
  // Middle position
  const middleDom = new JSDOM(dom.serialize());
  const middleDoc = middleDom.window.document;
  const middleTargetCard = middleDoc.querySelector(specificCardSelector);
  if (middleTargetCard) {
    const middleResultsContainer = middleDoc.querySelector('ul.srp-results');
    if (middleResultsContainer) {
      middleTargetCard.remove();
      const allResults = middleResultsContainer.querySelectorAll('li.s-card');
      if (allResults.length > 0) {
        const middleIndex = Math.max(0, Math.floor(allResults.length / 2) - 2);
        const middleResult = allResults[middleIndex];
        middleResultsContainer.insertBefore(middleTargetCard, middleResult);
      }
    }
  }
  variations.push({ name: 'middle', html: middleDom.serialize() });
  
  // Last position
  const lastDom = new JSDOM(dom.serialize());
  const lastDoc = lastDom.window.document;
  const lastTargetCard = lastDoc.querySelector(specificCardSelector);
  if (lastTargetCard) {
    const lastResultsContainer = lastDoc.querySelector('ul.srp-results');
    if (lastResultsContainer) {
      lastTargetCard.remove();
      const allResults = lastResultsContainer.querySelectorAll('li.s-card');
      if (allResults.length > 0) {
        const lastResult = allResults[allResults.length - 1];
        lastResultsContainer.insertBefore(lastTargetCard, lastResult.nextSibling);
      } else {
        lastResultsContainer.appendChild(lastTargetCard);
      }
    }
  }
  variations.push({ name: 'last', html: lastDom.serialize() });
  
  return variations;
}

function createPositionVariations(dom, renderableCard, originalCard, specificCardSelector) {
  const variations = [];
  
  // Banner position
  const bannerDom = new JSDOM(dom.serialize());
  const bannerDoc = bannerDom.window.document;
  injectForceStyles(bannerDoc);
  
  const bannerAnchor = bannerDoc.querySelector('.srp-controls') || bannerDoc.querySelector('.srp-river-results') || bannerDoc.querySelector('ul.srp-results');
  if (bannerAnchor) {
    let bannerWrapper = bannerDoc.getElementById('webarena-placement-banner');
    if (!bannerWrapper) {
      bannerWrapper = bannerDoc.createElement('div');
      bannerWrapper.id = 'webarena-placement-banner';
    }
    
    let bannerCardClone = renderableCard.cloneNode(true);
    // Ensure cloned card has target marker
    bannerCardClone.setAttribute('data-target-card', 'true');
    const origInThisDoc = bannerDoc.querySelector(specificCardSelector);
    if (origInThisDoc) origInThisDoc.remove();
    
    bannerAnchor.parentNode.insertBefore(bannerWrapper, bannerAnchor);
    bannerWrapper.appendChild(bannerCardClone);
  }
  
  variations.push({ name: 'banner', html: bannerDom.serialize() });
  
  // Header position (from ebay_position)
  const headerDom = new JSDOM(dom.serialize());
  const headerDoc = headerDom.window.document;
  injectForceStyles(headerDoc);
  const headerContainer = headerDoc.createElement('div');
  headerContainer.id = 'webarena-placement-header';
  headerContainer.className = 'webarena-placement';
  headerContainer.style.cssText = 'background: #fff; padding: 15px; margin: 10px 0; border-radius: 8px; text-align: center; box-shadow: 0 4px 12px rgba(0,0,0,0.1);';
  const headerOriginalCard = headerDoc.querySelector(specificCardSelector);
  if (headerOriginalCard) {
    headerContainer.appendChild(headerOriginalCard);
    const headerSelectors = ['.s-page-header', 'header', '.gh-header', '.srp-main-content'];
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
  }
  variations.push({ name: 'header', html: headerDom.serialize() });
  
  // Sidebar position (from ebay_position)
  const sidebarDom = new JSDOM(dom.serialize());
  const sidebarDoc = sidebarDom.window.document;
  injectForceStyles(sidebarDoc);
  const sidebarOriginalCard = sidebarDoc.querySelector(specificCardSelector);
  if (sidebarOriginalCard) {
    sidebarOriginalCard.remove();
    const sidebarContainer = sidebarDoc.createElement('div');
    sidebarContainer.id = 'webarena-placement-sidebar';
    sidebarContainer.className = 'webarena-placement';
    const cardClone = sidebarOriginalCard.cloneNode(true);
    const reorganizedCard = sidebarDoc.createElement('div');
    reorganizedCard.className = 'webarena-reorganized-card';
    reorganizedCard.style.cssText = 'display: flex !important; flex-direction: column !important; width: 100% !important; background: #fff !important; border-radius: 8px !important; overflow: visible !important; box-shadow: 0 2px 8px rgba(0,0,0,0.1) !important;';
    const imageWrapper = sidebarDoc.createElement('div');
    imageWrapper.style.cssText = 'width: 100% !important; order: 1 !important;';
    const imageLink = cardClone.querySelector('a[href*="itm"]');
    const imgEl = cardClone.querySelector('.s-item__image') || cardClone.querySelector('[class*="image"]') || cardClone.querySelector('img')?.closest('a') || cardClone.querySelector('img')?.parentElement;
    if (imageLink && imageLink.querySelector('img')) {
      const clonedLink = imageLink.cloneNode(true);
      clonedLink.style.cssText = 'display: block !important; width: 100% !important; text-decoration: none !important;';
      clonedLink.querySelectorAll('img').forEach(img => { img.style.cssText = 'width: 100% !important; height: auto !important; display: block !important; object-fit: cover !important;'; });
      imageWrapper.appendChild(clonedLink);
    } else if (imgEl) {
      const cloned = imgEl.cloneNode(true);
      cloned.style.setProperty('width', '100%', 'important');
      cloned.style.setProperty('max-width', '100%', 'important');
      cloned.style.setProperty('display', 'block', 'important');
      cloned.querySelectorAll('img').forEach(img => { img.style.setProperty('width', '100%', 'important'); img.style.setProperty('height', 'auto', 'important'); img.style.setProperty('display', 'block', 'important'); });
      imageWrapper.appendChild(cloned);
    } else {
      cardClone.querySelectorAll('img').forEach(img => { const c = img.cloneNode(true); c.style.cssText = 'width: 100% !important; height: auto !important; display: block !important; object-fit: cover !important;'; imageWrapper.appendChild(c); });
    }
    const textWrapper = sidebarDoc.createElement('div');
    textWrapper.className = 'webarena-text-container';
    textWrapper.style.cssText = 'width: 100% !important; padding: 12px 12px 16px 12px !important; order: 2 !important; box-sizing: border-box !important; display: flex !important; flex-direction: column !important; align-items: stretch !important; overflow: visible !important;';
    const textCardClone = sidebarOriginalCard.cloneNode(true);
    textCardClone.querySelectorAll('img').forEach(img => img.remove());
    const imgContainer = textCardClone.querySelector('a[href*="itm"]') || textCardClone.querySelector('.s-item__image') || textCardClone.querySelector('[class*="s-item__image"]');
    if (imgContainer && !imgContainer.textContent.trim()) imgContainer.remove();
    while (textCardClone.firstChild) textWrapper.appendChild(textCardClone.firstChild);
    reorganizedCard.appendChild(imageWrapper);
    reorganizedCard.appendChild(textWrapper);
    sidebarContainer.appendChild(reorganizedCard);
    textWrapper.querySelectorAll('div').forEach(el => { el.style.setProperty('display', 'block', 'important'); el.style.setProperty('width', '100%', 'important'); el.style.setProperty('max-width', '100%', 'important'); el.style.setProperty('overflow', 'visible', 'important'); });
    let leftSidebar = null;
    for (const el of sidebarDoc.querySelectorAll('*')) {
      if (el.textContent && el.textContent.includes('Category') && (el.classList.contains('srp-refine') || el.closest('.srp-refine') || el.closest('[class*="refine"]') || el.closest('[class*="filter"]'))) {
        leftSidebar = el.closest('.srp-refine') || el.closest('[class*="refine"]') || el;
        break;
      }
    }
    if (!leftSidebar) {
      for (const selector of ['.srp-refine', '.srp-filters', '.left-panel', '[data-testid="refine-panel"]', '.refine-panel', '.srp-left-column', '.left-column', '[class*="refine"]', '[class*="filter"]']) {
        leftSidebar = sidebarDoc.querySelector(selector);
        if (leftSidebar) break;
      }
    }
    if (leftSidebar && leftSidebar.parentNode) {
      const leftSidebarWidth = leftSidebar.offsetWidth || leftSidebar.getBoundingClientRect().width;
      if (leftSidebarWidth > 0) { sidebarContainer.style.setProperty('width', `${leftSidebarWidth}px`, 'important'); sidebarContainer.style.setProperty('max-width', `${leftSidebarWidth}px`, 'important'); }
      leftSidebar.parentNode.insertBefore(sidebarContainer, leftSidebar);
    } else {
      const mainContent = sidebarDoc.querySelector('.srp-main-content') || sidebarDoc.querySelector('main') || sidebarDoc.querySelector('#mainContent');
      if (mainContent) {
        const contentWrapper = mainContent.querySelector('.srp-results')?.parentNode || mainContent.querySelector('.results')?.parentNode || mainContent;
        const leftColumn = contentWrapper.querySelector('.left-column') || contentWrapper.querySelector('.srp-left-column') || contentWrapper.querySelector('[class*="left"]');
        if (leftColumn && leftColumn.parentNode) {
          if (leftColumn.firstChild) leftColumn.insertBefore(sidebarContainer, leftColumn.firstChild);
          else leftColumn.appendChild(sidebarContainer);
        } else {
          if (contentWrapper.firstChild) contentWrapper.insertBefore(sidebarContainer, contentWrapper.firstChild);
          else contentWrapper.appendChild(sidebarContainer);
        }
      } else {
        sidebarDoc.body.insertBefore(sidebarContainer, sidebarDoc.body.firstChild);
      }
    }
  }
  variations.push({ name: 'sidebar', html: sidebarDom.serialize() });
  
  return variations;
}

function createStyleVariations(baseHtml, cardSelector, variationType, variations, titleSelectors) {
  const results = [];
  
  variations.forEach(variation => {
    const dom = new JSDOM(baseHtml);
    const doc = dom.window.document;
    
    const card = doc.querySelector(cardSelector);
    if (!card) {
      console.log(`Selector ${cardSelector} matched no element`);
      dom.window.close();
      return;
    }
    
    console.log(`Found target element, applying ${variationType} variant: ${variation.name || variation.value}`);
    
    let success = false;
    
    if (variationType === 'background') {
      card.style.setProperty('background-color', variation.value, 'important');
      console.log(`Applied background color: ${variation.value}`);
      success = true;
    } else if (variationType === 'textColor') {
      success = applyTitleStyle(card, titleSelectors, 'color', variation.value);
    } else if (variationType === 'fontFamily') {
      success = applyTitleStyle(card, titleSelectors, 'font-family', variation.value);
    } else if (variationType === 'fontSize') {
      success = applyTitleStyle(card, titleSelectors, 'font-size', variation);
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

function createImageClarityVariations(baseHtml, cardSelector) {
  const results = [];
  
  UNIFIED_VARIATIONS.imageClarity.forEach(({ name, filter }) => {
    const dom = new JSDOM(baseHtml);
    const doc = dom.window.document;
    
    const success = applyImageStyle(doc, cardSelector, 'filter', filter);
    if (success) {
      const html = dom.serialize();
      results.push({ name: `image_clarity_${name}`, html: html });
    }
    
    dom.window.close();
  });
  
  return results;
}

function createCardClarityVariations(baseHtml, cardSelector) {
  const results = [];
  
  UNIFIED_VARIATIONS.cardClarity.forEach(({ name, filter }) => {
    const dom = new JSDOM(baseHtml);
    const doc = dom.window.document;
    
    const success = applyCardStyle(doc, cardSelector, 'filter', filter);
    if (success) {
      const html = dom.serialize();
      results.push({ name: `card_clarity_${name}`, html: html });
    }
    
    dom.window.close();
  });
  
  return results;
}

function createCardSizeVariations(baseHtml, cardSelector) {
  const results = [];
  
  UNIFIED_VARIATIONS.cardSizes.forEach(({ name, scale }) => {
    const dom = new JSDOM(baseHtml);
    const doc = dom.window.document;
    
    const success = applyCardStyle(doc, cardSelector, 'scale', scale);
    if (success) {
      const html = dom.serialize();
      results.push({ name: `card_size_${name}`, html: html });
    }
    
    dom.window.close();
  });
  
  return results;
}

(async () => {
  const snapshotArgIdx = process.argv.findIndex(a => a === '--snapshot');
  const snapshotPath = snapshotArgIdx > -1 ? process.argv[snapshotArgIdx + 1] : path.resolve(__dirname, 'source/Earphones.html');
  if (!snapshotPath || !fs.existsSync(snapshotPath)) {
    console.error('Snapshot file not found:', snapshotPath);
    process.exit(1);
  }

  const rawHtml = readSnapshot(snapshotPath);
  const dom = new JSDOM(rawHtml);
  const doc = dom.window.document;

  ensureBaseHref(dom);
  
  upgradeImageQuality(doc);
  
  removeNodes(doc, [
    '.s-item__watchheart'
  ]);
  
  const footers = doc.querySelectorAll('.s-card__footer');
  footers.forEach(footer => {
    const children = Array.from(footer.children);
    children.forEach(child => {
      // Keep elements with .s-card__sep, remove others
      if (!child.querySelector('.s-card__sep') && !child.classList.contains('s-card__sep')) {
        child.remove();
      }
    });
  });

  const allItems = doc.querySelectorAll('ul.srp-results li.s-card');
  console.log(`Found ${allItems.length} items`);
  
  if (allItems.length === 0) {
    console.error('No item card found.');
    process.exit(1);
  }
  
  const originalCard = allItems[0];
  console.log('Selected first item as target card');
  
  const renderableCard = extractRenderableCard(originalCard);
  if (!renderableCard) {
    console.error('Failed to extract card.');
    process.exit(1);
  }

  originalCard.setAttribute('data-target-card', 'true');
  console.log('Set data-target-card="true" on target item');
  
  const listingId = originalCard.getAttribute('data-listingid');
  let specificCardSelector;
  if (listingId) {
    specificCardSelector = `li.s-card[data-listingid="${listingId}"]`;
  } else {
    specificCardSelector = `li.s-card[data-target-card="true"]`;
  }
  console.log(`Using selector: ${specificCardSelector}`);

  const outputArgIdx = process.argv.findIndex(a => a === '--output');
  const outputDirRaw = outputArgIdx > -1 ? process.argv[outputArgIdx + 1] : null;
  const outputDir = outputDirRaw ? path.resolve(process.cwd(), outputDirRaw) : path.resolve(__dirname, 'output_ebay_unified');
  fse.ensureDirSync(outputDir);

  injectForceStyles(doc);

  const titleSelectors = [
    '.s-card__title',
    '.s-card__title span',
    'div[role="heading"].s-card__title',
    '.su-styled-text.primary',
    'div[aria-level="3"]'
  ];

  const originalPath = path.join(outputDir, 'original.html');
  writeOut(dom.serialize(), originalPath);

  let totalVariations = 1;

  console.log('Creating position variants...');
  const positionVariations = createPositionVariations(dom, renderableCard, originalCard, specificCardSelector);
  positionVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `position_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += positionVariations.length;

  console.log('Creating card order variants...');
  const cardOrderVariations = createCardOrderVariations(dom, specificCardSelector);
  cardOrderVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `order_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += cardOrderVariations.length;

  console.log('Creating background color variants...');
  const backgroundVariations = createStyleVariations(dom.serialize(), specificCardSelector, 'background', UNIFIED_VARIATIONS.backgroundColors, titleSelectors);
  backgroundVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += backgroundVariations.length;

  console.log('Creating text color variants...');
  const textColorVariations = createStyleVariations(dom.serialize(), specificCardSelector, 'textColor', UNIFIED_VARIATIONS.textColors, titleSelectors);
  textColorVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += textColorVariations.length;

  console.log('Creating font family variants...');
  const fontFamilyVariations = createStyleVariations(dom.serialize(), specificCardSelector, 'fontFamily', UNIFIED_VARIATIONS.fontFamilies, titleSelectors);
  fontFamilyVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += fontFamilyVariations.length;

  console.log('Creating font size variants...');
  const fontSizeVariations = createStyleVariations(dom.serialize(), specificCardSelector, 'fontSize', UNIFIED_VARIATIONS.fontSizes, titleSelectors);
  fontSizeVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += fontSizeVariations.length;

  console.log('Creating image clarity variants...');
  const imageClarityVariations = createImageClarityVariations(dom.serialize(), specificCardSelector);
  imageClarityVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += imageClarityVariations.length;

  console.log('Creating card clarity variants...');
  const cardClarityVariations = createCardClarityVariations(dom.serialize(), specificCardSelector);
  cardClarityVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += cardClarityVariations.length;

  console.log('Creating card size variants...');
  const cardSizeVariations = createCardSizeVariations(dom.serialize(), specificCardSelector);
  cardSizeVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += cardSizeVariations.length;

  console.log('eBay unified variant generation complete.');
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
})();

