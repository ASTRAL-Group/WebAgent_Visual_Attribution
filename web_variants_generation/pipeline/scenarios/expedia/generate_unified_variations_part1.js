const fs = require('fs');
const fse = require('fs-extra');
const path = require('path');
const { JSDOM } = require('jsdom');

// Unified variation configuration - part 1
const UNIFIED_VARIATIONS_PART1 = {
  // Background color variants (12)
  backgroundColors: [
    { name: '2196f3', value: '#2196f3' },
    { name: '1976d2', value: '#1976d2' }, // Darker blue, similar to 2196f3
    { name: '42a5f5', value: '#42a5f5' }, // Lighter blue, similar to 2196f3
    { name: '4caf50', value: '#4caf50' },
    { name: 'e91e63', value: '#e91e63' },
    { name: 'ffeb3b', value: '#ffeb3b' },
    { name: '6f42c1', value: '#6f42c1' },
    { name: 'ff9800', value: '#ff9800' },
    { name: '00bcd4', value: '#00bcd4' },
    { name: 'f44336', value: '#f44336' }, // Red
    { name: '9c27b0', value: '#9c27b0' }, // Purple
    { name: '000000', value: '#000000' }  // Black - extreme case
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
  fontSizes: [
    { name: '14px', value: '14px' },
    { name: '16px', value: '16px' },
    { name: '18px', value: '18px' },
    { name: '20px', value: '20px' },
    { name: '22px', value: '22px' },
    { name: '24px', value: '24px' }
  ],
  
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
    /* Force basic styles so variants render correctly */
    * {
      box-sizing: border-box !important;
    }
    
    .uitk-card {
      position: relative !important;
    }
    
    /* Ensure background-color variants are visible on the whole card */
    .uitk-card[style*="background-color"] {
      background-color: inherit !important;
      background: inherit !important;
    }
    
    /* Ensure card content area inherits background color */
    .uitk-card[style*="background-color"] .uitk-card-content-section,
    .uitk-card[style*="background-color"] .uitk-layout-flex,
    .uitk-card[style*="background-color"] .uitk-layout-grid {
      background-color: inherit !important;
      background: inherit !important;
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

function sanitizeColorForFilename(colorValue) {
  // Remove '#' character and return the hex value without it
  return colorValue.replace('#', '');
}

function applyTextStyle(card, titleSelectors, prop, value) {
  let success = false;
  
  console.log(`🔍 Searching for text elements, property: ${prop}, value: ${value}`);
  
  // First try generic text element selectors
  const genericSelectors = [
    'span', 'div', 'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'a', 'strong', 'em', 'b', 'i'
  ];
  
  // Expedia-specific text selectors
  const expediaSelectors = [
    '.uitk-text',
    '.uitk-type-300',
    '.uitk-type-400', 
    '.uitk-type-500',
    '.uitk-text-default-theme',
    '.uitk-text-standard-theme',
    '.uitk-type-bold',
    '.uitk-type-medium'
  ];
  
  // Combine selectors from config and defaults
  const allSelectors = [...genericSelectors, ...titleSelectors, ...expediaSelectors];
  
  for (const selector of allSelectors) {
    const elements = card.querySelectorAll(selector);
    if (elements.length > 0) {
      elements.forEach(el => {
        // Only apply styles to elements that actually contain text
        if (el.textContent && el.textContent.trim().length > 0) {
          el.style.setProperty(prop, value, 'important');
          console.log(`✅ Applied style to element: ${el.tagName}.${el.className} - ${prop} = ${value}`);
        }
      });
      success = true;
    }
  }
  
  if (!success) {
    console.log('❌ No text elements found to apply style');
  }
  
  return success;
}


function createStyleVariations(baseHtml, cfg, targetHotelName, variationType, variations) {
  const results = [];
  
  variations.forEach(variation => {
    const dom = new JSDOM(baseHtml);
    const doc = dom.window.document;
    
    // Re-locate the target hotel card
    let targetCard = null;
    let targetIndex = -1;
    
    // Method 1: use .uitk-card selector to find all cards
    const allCards = doc.querySelectorAll('.uitk-card');
    
    for (let i = 0; i < allCards.length; i++) {
      const card = allCards[i];
      const cardText = card.textContent || '';
      
      // Find a card that contains the target hotel name and no other hotels
      if (cardText.includes(targetHotelName) && 
          cardText.length > 200 && 
          cardText.length < 2000 &&
          !cardText.includes('Heritage Hotel') &&
          !cardText.includes('The Belvedere')) {
        targetCard = card;
        targetIndex = i;
        console.log(`✅ Found target hotel card (uitk-card ${i}): ${targetHotelName}`);
        break;
      }
    }
    
    if (!targetCard) {
      // Method 2: fall back to [data-stid*="property-listing"] selector
      const propertyCards = doc.querySelectorAll('[data-stid*="property-listing"]');
      
      for (let i = 0; i < propertyCards.length; i++) {
        const card = propertyCards[i];
        const cardText = card.textContent || '';
        
        if (cardText.includes(targetHotelName)) {
          // Inside a large card, search for the specific Montage Big Sky hotel sub-element
          const heritageElements = card.querySelectorAll('*');
          
          for (const element of heritageElements) {
            const elementText = element.textContent || '';
            // Look for elements that contain "Montage Big Sky" with reasonable text length
            if (elementText.includes(targetHotelName) && 
                elementText.length > 100 && 
                elementText.length < 10000) {
              targetCard = element;
              targetIndex = i;
              console.log(`✅ Found Montage Big Sky sub-element: ${targetHotelName} (index: ${i})`);
              break;
            }
          }
          
          if (!targetCard) {
            targetCard = card;
            targetIndex = i;
            console.log(`⚠️  Using large card as target: ${targetHotelName} (index: ${i})`);
          }
          break;
        }
      }
    }
    
    if (!targetCard) {
      console.log(`❌ Target hotel not found: ${targetHotelName}`);
      dom.window.close();
      return;
    }

    console.log(`✅ Ready to apply ${variationType} variation: ${variation.name} to target card`);
    
    let success = false;
    
    if (variationType === 'background') {
      // Find the full card container instead of a sub-element
      let cardContainer = targetCard;
      
      // If the current element is not the card container, walk up the DOM to find it
      if (!cardContainer.classList.contains('uitk-card')) {
        let parent = cardContainer.parentElement;
        while (parent && !parent.classList.contains('uitk-card')) {
          parent = parent.parentElement;
        }
        if (parent) {
          cardContainer = parent;
        }
      }
      
      // Apply background color to the entire card container
      cardContainer.style.setProperty('background-color', variation.value, 'important');
      cardContainer.style.setProperty('background', variation.value, 'important');
      cardContainer.style.setProperty('position', 'relative', 'important');
      cardContainer.style.setProperty('z-index', '1', 'important');
      
      // Ensure card content sections also have background color
      const contentSections = cardContainer.querySelectorAll('.uitk-card-content-section, .uitk-layout-flex, .uitk-layout-grid');
      contentSections.forEach(section => {
        section.style.setProperty('background-color', variation.value, 'important');
        section.style.setProperty('background', variation.value, 'important');
      });
      
      console.log(`🎨 Applied background color: ${variation.value} to full card container`);
      console.log(`   Target card text snippet: ${cardContainer.textContent.substring(0, 100)}...`);
      console.log(`   Applied style: ${cardContainer.getAttribute('style')}`);
      success = true;
    } else if (variationType === 'textColor') {
      success = applyTextStyle(targetCard, cfg.titleSelectors, 'color', variation.value);
    } else if (variationType === 'fontFamily') {
      success = applyTextStyle(targetCard, cfg.titleSelectors, 'font-family', variation.value);
    } else if (variationType === 'fontSize') {
      success = applyTextStyle(targetCard, cfg.titleSelectors, 'font-size', variation.value);
    }
    
    if (success) {
      const html = dom.serialize();
      results.push({ name: `${variationType}_${variation.name}`, html: html });
    console.log(`✅ Successfully created variation: ${variationType}_${variation.name}`);
    } else {
    console.log(`❌ Failed to apply variation: ${variationType}_${variation.name}`);
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
        
        // Inside the large card, search for the specific Montage Big Sky hotel sub-element
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

  // Build a specific selector for the target hotel card
  const specificCardSelector = `.uitk-card:nth-child(${cardIndex + 1})`;
  console.log(`🎯 Using specific selector: ${specificCardSelector}`);

  // Configure output directory
  const outputDir = path.resolve(__dirname, 'output_expedia2_unified_complete');
  fse.ensureDirSync(outputDir);

  // Apply forced styles to base page
  injectForceStyles(doc);

  // Create original file
  const originalPath = path.join(outputDir, 'original.html');
  writeOut(dom.serialize(), originalPath);

  let totalVariations = 1; // original

  // Create background color variants
  console.log('🎨 Creating background color variants...');
  const backgroundVariations = createStyleVariations(dom.serialize(), cfg, targetHotelName, 'background', UNIFIED_VARIATIONS_PART1.backgroundColors);
  backgroundVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += backgroundVariations.length;

  // Create text color variants
  console.log('🎨 Creating text color variants...');
  const textColorVariations = createStyleVariations(dom.serialize(), cfg, targetHotelName, 'textColor', UNIFIED_VARIATIONS_PART1.textColors);
  textColorVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += textColorVariations.length;

  // Create font family variants
  console.log('🎨 Creating font family variants...');
  const fontFamilyVariations = createStyleVariations(dom.serialize(), cfg, targetHotelName, 'fontFamily', UNIFIED_VARIATIONS_PART1.fontFamilies);
  fontFamilyVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += fontFamilyVariations.length;

  // Create font size variants
  console.log('🎨 Creating font size variants...');
  const fontSizeVariations = createStyleVariations(dom.serialize(), cfg, targetHotelName, 'fontSize', UNIFIED_VARIATIONS_PART1.fontSizes);
  fontSizeVariations.forEach(variation => {
    const outputPath = path.join(outputDir, `style_${variation.name}.html`);
    writeOut(variation.html, outputPath);
  });
  totalVariations += fontSizeVariations.length;


  console.log('🎉 Expedia part 1 variants generation completed');
  console.log(`Total files generated: ${totalVariations}`);
  console.log('Variant breakdown:');
  console.log(`- Original: 1`);
  console.log(`- Background color variants: ${backgroundVariations.length}`);
  console.log(`- Text color variants: ${textColorVariations.length}`);
  console.log(`- Font variants: ${fontFamilyVariations.length}`);
  console.log(`- Font size variants: ${fontSizeVariations.length}`);
})();
