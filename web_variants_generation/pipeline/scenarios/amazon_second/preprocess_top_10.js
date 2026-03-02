const fs = require('fs');
const fse = require('fs-extra');
const path = require('path');
const { JSDOM } = require('jsdom');

// Hardcoded config (originally read from config_amazon.json)
const CONFIG = {
  baseHref: 'https://www.amazon.com/',
  addBaseTag: true,
  stripScripts: true,
  ignoreSelectors: [
    '#navBackToTop',
    '.nav-footer-line',
    '.navFooterBackToTop',
    '.navFooterLine',
    '.aok-inline-block.s-sponsored-label-info-icon',
    '[data-component-type="s-searchgrid-carousel"]',
    '[data-cel-widget="s-ads"]',
    '.AdHolder'
  ]
};

function readSnapshot(snapshotPath) {
  const html = fs.readFileSync(snapshotPath, 'utf-8');
  return html.replace(/<script[\s\S]*?<\/script>/gi, '');
}

function ensureBaseHref(dom) {
  if (!CONFIG.addBaseTag) return;
  const doc = dom.window.document;
  let base = doc.querySelector('base');
  if (!base) {
    base = doc.createElement('base');
    doc.head.appendChild(base);
  }
  base.setAttribute('href', CONFIG.baseHref);
}

function stripScriptsIfNeeded(html) {
  if (!CONFIG.stripScripts) return html;
  
  const ignoreSelectors = CONFIG.ignoreSelectors || [];
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

function writeOut(html, outPath) {
  fse.ensureDirSync(path.dirname(outPath));
  fs.writeFileSync(outPath, html, 'utf-8');
  console.log(`Exported: ${outPath}`);
}

function adjustSidebarHeight(doc, lastVisibleProduct, maxProducts) {
  /**
   * Adjust sidebar: keep "Features" and all filter categories before it, hide the rest
   */
  console.log(`Adjusting sidebar...`);
  
  // Find sidebar element - Amazon sidebar is usually #s-refinements
  const sidebarSelectors = [
    '#s-refinements',
    '[id*="refinements"]',
    '.s-refinements',
    '[class*="refinements"]'
  ];
  
  let sidebar = null;
  for (const selector of sidebarSelectors) {
    sidebar = doc.querySelector(selector);
    if (sidebar) {
      console.log(`Found sidebar: ${selector}`);
      break;
    }
  }
  
  if (!sidebar) {
    console.log(`Sidebar element not found, skipping adjustment`);
    return;
  }
  
  // Find all filter categories
  // Amazon filter categories are usually div[role="group"], each with a heading
  const filterCategories = sidebar.querySelectorAll('div[role="group"]');
  console.log(`Found ${filterCategories.length} filter categories`);
  
  if (filterCategories.length === 0) {
    console.log(`No filter categories found, skipping adjustment`);
    return;
  }
  
  // Find index of "Features" category
  let featuresIndex = -1;
  for (let i = 0; i < filterCategories.length; i++) {
    const category = filterCategories[i];
    // Find heading text inside category
    const heading = category.querySelector('[role="heading"] span, [role="heading"]');
    if (heading) {
      const headingText = heading.textContent.trim();
      // Check if it contains "Features" (case-insensitive)
      if (headingText.toLowerCase().includes('features') && 
          !headingText.toLowerCase().includes('sustainability')) {
        featuresIndex = i;
        console.log(`Found "Features" category, index: ${featuresIndex}`);
        console.log(`   Heading text: "${headingText}"`);
        break;
      }
    }
  }
  
  if (featuresIndex === -1) {
    console.log(`"Features" category not found, skipping hide`);
    return;
  }
  
  // Hide all categories after "Features"
  let hiddenCount = 0;
  for (let i = featuresIndex + 1; i < filterCategories.length; i++) {
    const category = filterCategories[i];
    category.style.setProperty('display', 'none', 'important');
    hiddenCount++;
    
    // Get category heading for log
    const heading = category.querySelector('[role="heading"] span, [role="heading"]');
    const headingText = heading ? heading.textContent.trim() : `Category ${i + 1}`;
    console.log(`Hiding category: "${headingText}"`);
  }
  
  console.log(`Sidebar adjustment done: kept ${featuresIndex + 1} categories (including "Features"), hidden ${hiddenCount}`);
}

function createTopNProductsVariation(baseHtml, maxProducts = 10) {
  const dom = new JSDOM(baseHtml);
  const doc = dom.window.document;
  
  console.log(`Creating variation that keeps only the first ${maxProducts} products...`);
  
  // Find all product cards - Amazon products usually have data-asin
  const allProducts = doc.querySelectorAll('[data-asin]');
  console.log(`Found ${allProducts.length} elements with data-asin`);
  
  // Filter valid product cards (exclude empty/invalid ones)
  const productCards = [];
  for (let i = 0; i < allProducts.length; i++) {
    const product = allProducts[i];
    const asin = product.getAttribute('data-asin');
    
    // Filter out invalid ASINs
    if (asin && asin !== '' && asin !== 'undefined' && !asin.includes('sponsor')) {
      // Further check if this is a valid product card (usually has title/price text)
      const textContent = product.textContent || '';
      if (textContent.length > 50 && textContent.length < 5000) {
        productCards.push(product);
      }
    }
  }
  
  console.log(`Found ${productCards.length} valid product cards`);
  
  // Keep only the first maxProducts product cards, hide the rest
  let visibleCount = 0;
  let lastVisibleProduct = null;
  
  for (let i = 0; i < productCards.length; i++) {
    const product = productCards[i];
    
    if (visibleCount < maxProducts) {
      // Keep visible (no display override)
      console.log(`Keeping product card #${visibleCount + 1} visible`);
      lastVisibleProduct = product; // remember last visible product
      visibleCount++;
    } else {
      // Hide product
      product.style.setProperty('display', 'none', 'important');
      console.log(`Hiding product card #${visibleCount + 1}`);
    }
  }
  
  console.log(`Processed ${visibleCount} visible, ${productCards.length - visibleCount} hidden`);
  
  // Adjust sidebar height to roughly match product area
  adjustSidebarHeight(doc, lastVisibleProduct, maxProducts);
  
  const html = dom.serialize();
  dom.window.close();
  
  return html;
}

(async () => {
  const snapshotArgIdx = process.argv.findIndex(a => a === '--snapshot');
  const snapshotPath = snapshotArgIdx > -1
    ? path.resolve(process.cwd(), process.argv[snapshotArgIdx + 1])
    : path.resolve(__dirname, 'source/Amazon.com _ laptop.html');
  if (!snapshotPath || !fs.existsSync(snapshotPath)) {
    console.error('Snapshot path required and must exist. Use --snapshot <path>');
    process.exit(1);
  }

  const outputArgIdx = process.argv.findIndex(a => a === '--output');
  const outputDirRaw = outputArgIdx > -1 ? process.argv[outputArgIdx + 1] : null;
  const outputDir = outputDirRaw
    ? path.resolve(process.cwd(), outputDirRaw)
    : path.resolve(__dirname, '../../data/amazon_second');
  fse.ensureDirSync(outputDir);

  const rawHtml = readSnapshot(snapshotPath);
  const html = stripScriptsIfNeeded(rawHtml);
  const dom = new JSDOM(html);
  const doc = dom.window.document;
  ensureBaseHref(dom);
  removeNodes(doc, CONFIG.ignoreSelectors || []);

  const top10Html = createTopNProductsVariation(dom.serialize(), 10);
  const outputPath = path.join(outputDir, 'top_10_products.html');
  writeOut(top10Html, outputPath);
  console.log('Preprocess done. Output:', outputPath);
})();
