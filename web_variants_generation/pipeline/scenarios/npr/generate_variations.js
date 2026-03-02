const fs = require('fs');
const fse = require('fs-extra');
const path = require('path');
const { JSDOM } = require('jsdom');

// Unified variation configuration
const UNIFIED_VARIATIONS = {
  // Position variants
  positions: [
    { name: 'header', description: 'At very top of page, above NPR header' },
    { name: 'banner', description: 'Top-of-page banner in main content area' }
  ],

  // Card order variants (3)
  cardOrders: [
    { name: 'first', description: 'Move selected card to first position' },
    { name: 'middle', description: 'Move selected card to middle position' },
    { name: 'last', description: 'Move selected card to last position' }
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
    { name: '394f78', value: '#394f78' }  // Background color - extreme case
  ],

  // Text color variants (6)
  textColors: [
    { name: '111111', value: '#111111' },
    { name: '0d6efd', value: '#0d6efd' },
    { name: 'dc3545', value: '#dc3545' },
    { name: '198754', value: '#198754' },
    { name: '6f42c1', value: '#6f42c1' },
    { name: 'ffffff', value: '#ffffff' }  // White - extreme case
  ],

  // Font family variants (13)
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
    // { name: 'scale_1.5', scale: 1.5 }  // Commented out
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
  base.setAttribute('href', 'https://www.npr.org/');
}

function removeNodes(doc, selectors = []) {
  selectors.forEach(sel => {
    doc.querySelectorAll(sel).forEach(n => n.remove());
  });
}

function upgradeImageQuality(doc) {
  // NPR images may use data-original or data-template attributes
  const images = doc.querySelectorAll('img');
  images.forEach(img => {
    // Prefer high-resolution URL from data-original when present
    const originalUrl = img.getAttribute('data-original');
    if (originalUrl) {
      img.setAttribute('src', originalUrl);
      img.removeAttribute('data-original');
      img.removeAttribute('loading');
    }
    // If data-template exists, also try to use it
    const templateUrl = img.getAttribute('data-template');
    if (templateUrl && !img.getAttribute('src')) {
      // Try to extract URL from template (simplified)
      img.setAttribute('src', templateUrl.split('?url=')[1] || templateUrl);
    }
  });
}

function extractRenderableCard(card) {
  if (!card) return null;
  // NPR article card structure: the entire <article> element
  return card.cloneNode(true);
}

function injectForceStyles(doc) {
  const style = doc.createElement('style');
  style.textContent = `
  #npr-placement-header, #npr-placement-banner {
    display: block !important;
    visibility: visible !important;
    opacity: 1 !important;
    box-sizing: border-box !important;
    width: 100% !important;
    max-width: 100% !important;
    overflow: visible !important;
  }

  #npr-placement-header *, #npr-placement-banner * {
    visibility: visible !important;
    opacity: 1 !important;
  }

  #npr-placement-header article,
  #npr-placement-header .story-wrap,
  #npr-placement-banner article,
  #npr-placement-banner .story-wrap {
    display: block !important;
  }

  #npr-placement-header [hidden],
  #npr-placement-banner [hidden] {
    display: initial !important;
  }

  /* Ensure banner container content is fully visible and centered */
  #npr-placement-banner .bucketwrap {
    display: inline-block !important;
    margin: 0 auto !important;
  }

  #npr-placement-banner img,
  #npr-placement-banner picture,
  #npr-placement-banner figure {
    max-width: 100% !important;
    height: auto !important;
  }

  /* Add spacing between article cards */
  #main-section .bucketwrap article {
    margin-bottom: 16px !important;
  }
  `;
  doc.head.appendChild(style);
}

function writeOut(html, outPath) {
  fse.ensureDirSync(path.dirname(outPath));
  fs.writeFileSync(outPath, html, 'utf-8');
  console.log(`✅ Exported: ${outPath}`);
}

function applyTitleStyle(card, titleSelectors, prop, value) {
  let success = false;
  
  console.log(`🔍 Searching for text elements, property: ${prop}, value: ${value}`);
  
  // NPR title selectors
  const textSelectors = [
    '.title',
    'h3.title',
    '.story-text h3',
    '.story-text .title',
    '.teaser',
    'p.teaser'
  ];
  
  const allSelectors = [...titleSelectors, ...textSelectors];
  
  for (const selector of allSelectors) {
    const elements = card.querySelectorAll(selector);
    if (elements.length > 0) {
      elements.forEach(el => {
        if (el.textContent && el.textContent.trim().length > 0) {
          el.style.setProperty(prop, value, 'important');
          console.log(`✅ Applied style to element: ${el.tagName}.${el.className} - ${prop} = ${value}`);
          success = true;
        }
      });
    }
  }
  
  if (!success) {
    console.log('❌ No text elements found to apply style');
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
    // Do not apply inherit filter to image elements or their containers; keep them visible
    const allElements = card.querySelectorAll('*');
    allElements.forEach(el => {
      // Skip image elements and their containers so they stay visible (may be blurred but not fully hidden)
      const isImageElement = el.tagName === 'IMG' || el.tagName === 'PICTURE' || el.tagName === 'SOURCE';
      const isImageContainer = el.classList.contains('imagewrap') || 
                               el.classList.contains('thumb-image') || 
                               el.tagName === 'FIGURE';
      if (!isImageElement && !isImageContainer) {
        el.style.setProperty('filter', 'inherit', 'important');
      }
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
    // Find the first bucketwrap container that holds the target card
    let container = targetCard.closest('.bucketwrap');
    if (container && container.parentNode) {
      const parent = container.parentNode;
      container.remove();
      // Find the first bucketwrap container in the parent
      const firstContainer = parent.querySelector('.bucketwrap');
      if (firstContainer) {
        parent.insertBefore(container, firstContainer);
      } else {
        parent.insertBefore(container, parent.firstChild);
      }
    }
  }
  variations.push({ name: 'first', html: firstDom.serialize() });
  
  // Middle position - move between 4th and 5th small-news items (between first and second rows)
  const middleDom = new JSDOM(dom.serialize());
  const middleDoc = middleDom.window.document;
  const middleTargetCard = middleDoc.querySelector(specificCardSelector);
  if (middleTargetCard) {
    let container = middleTargetCard.closest('.bucketwrap');
    if (container && container.parentNode) {
      const parent = container.parentNode;
      container.remove();
      
      // Find the contentWrap container
      const contentWrap = middleDoc.querySelector('#contentWrap');
      if (contentWrap) {
        // Find all spicerack containers (rows of small-news items)
        const spiceracks = Array.from(contentWrap.querySelectorAll('.bucketwrap.spicerack.featured.area'));
        
        if (spiceracks.length >= 1) {
          // Insert after the first spicerack (i.e. between 4th and 5th small-news items)
          const firstSpicerack = spiceracks[0];
          if (firstSpicerack && firstSpicerack.nextSibling) {
            contentWrap.insertBefore(container, firstSpicerack.nextSibling);
            console.log('✅ Middle: inserted after first spicerack (between 4th and 5th small-news items)');
          } else {
            // If there is no next sibling, append directly after the first spicerack
            if (firstSpicerack.parentNode === contentWrap) {
              contentWrap.insertBefore(container, firstSpicerack.nextSibling);
            } else {
              contentWrap.appendChild(container);
            }
          }
        } else {
          // If there is no spicerack, find all small-news cards and insert after the 4th
          const allPromoCards = Array.from(contentWrap.querySelectorAll('.bucketwrap.promocard'));
          const otherPromoCards = allPromoCards.filter(c => {
            const article = c.querySelector('article');
            return article && article !== middleTargetCard && !article.hasAttribute('data-target-card');
          });
          
          if (otherPromoCards.length >= 4) {
            const fourthCard = otherPromoCards[3];
            if (fourthCard && fourthCard.nextSibling) {
              fourthCard.parentNode.insertBefore(container, fourthCard.nextSibling);
            } else {
              contentWrap.appendChild(container);
            }
          } else {
            contentWrap.appendChild(container);
          }
        }
      } else {
        parent.appendChild(container);
      }
    }
  }
  variations.push({ name: 'middle', html: middleDom.serialize() });
  
  // Last position - move after the 8th small-news item (after the second row)
  const lastDom = new JSDOM(dom.serialize());
  const lastDoc = lastDom.window.document;
  const lastTargetCard = lastDoc.querySelector(specificCardSelector);
  if (lastTargetCard) {
    let container = lastTargetCard.closest('.bucketwrap');
    if (container && container.parentNode) {
      const parent = container.parentNode;
      container.remove();
      
      // Find the contentWrap container
      const contentWrap = lastDoc.querySelector('#contentWrap');
      if (contentWrap) {
        // Find all spicerack containers (rows of small-news items)
        const spiceracks = Array.from(contentWrap.querySelectorAll('.bucketwrap.spicerack.featured.area'));
        
        if (spiceracks.length >= 2) {
          // Insert after the second spicerack (i.e. after the 8th small-news item)
          const secondSpicerack = spiceracks[1];
          if (secondSpicerack && secondSpicerack.nextSibling) {
            contentWrap.insertBefore(container, secondSpicerack.nextSibling);
            console.log('✅ Last: inserted after second spicerack (after 8th small-news item)');
          } else {
            // If there is no next sibling, append directly after the second spicerack
            contentWrap.appendChild(container);
          }
        } else if (spiceracks.length === 1) {
          // If there is only one spicerack, insert after it
          const firstSpicerack = spiceracks[0];
          if (firstSpicerack && firstSpicerack.nextSibling) {
            contentWrap.insertBefore(container, firstSpicerack.nextSibling);
          } else {
            contentWrap.appendChild(container);
          }
        } else {
          // If there is no spicerack, find all small-news cards and insert after the 8th
          const allPromoCards = Array.from(contentWrap.querySelectorAll('.bucketwrap.promocard'));
          const otherPromoCards = allPromoCards.filter(c => {
            const article = c.querySelector('article');
            return article && article !== lastTargetCard && !article.hasAttribute('data-target-card');
          });
          
          if (otherPromoCards.length >= 8) {
            const eighthCard = otherPromoCards[7];
            if (eighthCard && eighthCard.nextSibling) {
              eighthCard.parentNode.insertBefore(container, eighthCard.nextSibling);
            } else {
              contentWrap.appendChild(container);
            }
          } else {
            contentWrap.appendChild(container);
          }
        }
      } else {
        parent.appendChild(container);
      }
    }
  }
  variations.push({ name: 'last', html: lastDom.serialize() });
  
  return variations;
}

function createPositionVariations(dom, renderableCard, originalCard, specificCardSelector) {
  const variations = [];

  // Header position - place card at very top of the page, above the NPR header
  const headerDom = new JSDOM(dom.serialize());
  const headerDoc = headerDom.window.document;
  injectForceStyles(headerDoc);

  // Find the original article card and its container (usually .bucketwrap)
  const originalHeaderCard = headerDoc.querySelector(specificCardSelector);
  if (originalHeaderCard) {
    let container = originalHeaderCard.closest('.bucketwrap');
    if (!container) {
      // If no .bucketwrap is found, fall back to the parent element
      container = originalHeaderCard.parentElement;
    }

    if (container && container.parentNode) {
      // Remove container from its original position
      container.remove();

      // Create header container
      const headerContainer = headerDoc.createElement('div');
      headerContainer.id = 'npr-placement-header';
      headerContainer.className = 'npr-placement';
      headerContainer.style.cssText = 'margin: 0; box-sizing: border-box;';

      // Move original container into the header container without changing its styles
      headerContainer.appendChild(container);

      // Insert at the very beginning of body so it appears above everything else
      const body = headerDoc.body;
      if (body.firstChild) {
        body.insertBefore(headerContainer, body.firstChild);
      } else {
        body.appendChild(headerContainer);
      }
    }
  }

  variations.push({ name: 'header', html: headerDom.serialize() });

  // Banner position - top-of-page banner with scaled card centered
  const bannerDom = new JSDOM(dom.serialize());
  const bannerDoc = bannerDom.window.document;
  injectForceStyles(bannerDoc);

  // Find the original article card and its container (usually .bucketwrap)
  const originalBannerCard = bannerDoc.querySelector(specificCardSelector);
  if (originalBannerCard) {
    let container = originalBannerCard.closest('.bucketwrap');
    if (!container) {
      // If no .bucketwrap is found, fall back to the parent element
      container = originalBannerCard.parentElement;
    }

    if (container && container.parentNode) {
      // Remove container from its original position
      container.remove();

      // Apply a scale effect (0.8x) to the original container
      container.style.transform = 'scale(0.8)';
      container.style.transformOrigin = 'center';

      // Create banner container, centered with constrained width
      const bannerContainer = bannerDoc.createElement('div');
      bannerContainer.id = 'npr-placement-banner';
      bannerContainer.className = 'npr-placement';
      bannerContainer.style.cssText =
        'padding: 15px; margin: 10px 0; border-radius: 8px; text-align: center; ' +
        'max-width: 600px; margin-left: auto; margin-right: auto; box-sizing: border-box;';

      // Move the original container (now scaled) into the banner container
      bannerContainer.appendChild(container);

      // Insert before the main content section if possible
      const mainSection = bannerDoc.querySelector('#main-section') || bannerDoc.querySelector('main');
      if (mainSection) {
        mainSection.parentNode.insertBefore(bannerContainer, mainSection);
      } else {
        // Fallback: insert at the top of body
        bannerDoc.body.insertBefore(bannerContainer, bannerDoc.body.firstChild);
      }
    }
  }

  variations.push({ name: 'banner', html: bannerDom.serialize() });

  return variations;
}

function createStyleVariations(baseHtml, cardSelector, variationType, variations, titleSelectors) {
  const results = [];
  
  variations.forEach(variation => {
    const dom = new JSDOM(baseHtml);
    const doc = dom.window.document;
    
    const card = doc.querySelector(cardSelector);
    if (!card) {
      console.log(`❌ No element found for selector ${cardSelector}`);
      dom.window.close();
      return;
    }
    
    console.log(`✅ Found target element, applying ${variationType} variation: ${variation.name || variation.value}`);
    
    let success = false;
    
    if (variationType === 'background') {
      card.style.setProperty('background-color', variation.value, 'important');
    console.log(`🎨 Applying background color: ${variation.value}`);
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
      console.log(`✅ Created variation: ${variationType}_${safeName}`);
    } else {
      console.log(`❌ Failed to apply variation: ${variationType}_${variation.name || variation.value}`);
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
  const snapshotPath = path.resolve(__dirname, 'source/npr.html');
  if (!fs.existsSync(snapshotPath)) {
    console.error('❌ Snapshot file not found:', snapshotPath);
    process.exit(1);
  }

  const rawHtml = readSnapshot(snapshotPath);
  const dom = new JSDOM(rawHtml);
  const doc = dom.window.document;

  ensureBaseHref(dom);
  
  // Upgrade image quality if possible
  upgradeImageQuality(doc);
  
  // Remove unwanted elements (optional, currently none configured)
  removeNodes(doc, [
    // Add selectors here if some elements should be stripped
  ]);

  const targetTitle = "Federal judge rules the U.S. violated due process with Alien Enemies Act deportations";
  const allArticles = doc.querySelectorAll('article');
  console.log(`✅ Found ${allArticles.length} articles in snapshot`);
  
  let originalCard = null;
  for (const article of allArticles) {
    const titleElement = article.querySelector('h3.title, .title');
    if (titleElement && titleElement.textContent.includes(targetTitle)) {
      originalCard = article;
      console.log(`✅ Found target article: "${titleElement.textContent.substring(0, 80)}..."`);
      break;
    }
  }
  
  if (!originalCard) {
    console.error('❌ Target article not found');
    process.exit(1);
  }
  
  // Extract a standalone renderable card
  const renderableCard = extractRenderableCard(originalCard);
  if (!renderableCard) {
    console.error('❌ Failed to extract card.');
    process.exit(1);
  }

  // Add a unique marker to the target article for later tracking
  originalCard.setAttribute('data-target-card', 'true');
  console.log('✅ Added marker to target article: data-target-card="true"');
  
  // Build a specific selector for the target article using the data-target-card marker
  const specificCardSelector = `article[data-target-card="true"]`;
  console.log(`🎯 Using specific selector: ${specificCardSelector}`);

  // Configure output directory (data/npr/html under repo root)
  const outputDir = path.resolve(process.cwd(), 'data/npr/html');
  fse.ensureDirSync(outputDir);

  // Add forced styles to the base page
  injectForceStyles(doc);

  // NPR title selectors for style variants
  const titleSelectors = [
    '.title',
    'h3.title',
    '.story-text h3',
    '.story-text .title'
  ];

  // Create original file
  const originalPath = path.join(outputDir, 'original.html');
  writeOut(dom.serialize(), originalPath);

  let totalVariations = 1; // original

  // Create position variants
  console.log('🎨 Creating position variants...');
  const positionVariations = createPositionVariations(dom, renderableCard, originalCard, specificCardSelector);
  positionVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `position_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += positionVariations.length;

  // Create card order variants
  console.log('🎨 Creating card order variants...');
  const cardOrderVariations = createCardOrderVariations(dom, specificCardSelector);
  cardOrderVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `order_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += cardOrderVariations.length;

  // Create background color variants
  console.log('🎨 Creating background color variants...');
  const backgroundVariations = createStyleVariations(dom.serialize(), specificCardSelector, 'background', UNIFIED_VARIATIONS.backgroundColors, titleSelectors);
  backgroundVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += backgroundVariations.length;

  // Create text color variants
  console.log('🎨 Creating text color variants...');
  const textColorVariations = createStyleVariations(dom.serialize(), specificCardSelector, 'textColor', UNIFIED_VARIATIONS.textColors, titleSelectors);
  textColorVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += textColorVariations.length;

  // Create font family variants
  console.log('🎨 Creating font family variants...');
  const fontFamilyVariations = createStyleVariations(dom.serialize(), specificCardSelector, 'fontFamily', UNIFIED_VARIATIONS.fontFamilies, titleSelectors);
  fontFamilyVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += fontFamilyVariations.length;

  // Create font size variants
  console.log('🎨 Creating font size variants...');
  const fontSizeVariations = createStyleVariations(dom.serialize(), specificCardSelector, 'fontSize', UNIFIED_VARIATIONS.fontSizes, titleSelectors);
  fontSizeVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += fontSizeVariations.length;

  // Create image clarity variants
  console.log('🎨 Creating image clarity variants...');
  const imageClarityVariations = createImageClarityVariations(dom.serialize(), specificCardSelector);
  imageClarityVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += imageClarityVariations.length;

  // Create card clarity variants
  console.log('🎨 Creating card clarity variants...');
  const cardClarityVariations = createCardClarityVariations(dom.serialize(), specificCardSelector);
  cardClarityVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += cardClarityVariations.length;

  // Create card size variants
  console.log('🎨 Creating card size variants...');
  const cardSizeVariations = createCardSizeVariations(dom.serialize(), specificCardSelector);
  cardSizeVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += cardSizeVariations.length;

  console.log('🎉 NPR unified variants generation completed');
  console.log(`Total files generated: ${totalVariations}`);
  console.log('Variant breakdown:');
  console.log('- Original: 1');
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

