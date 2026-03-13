const fs = require('fs');
const fse = require('fs-extra');
const path = require('path');
const { spawnSync } = require('child_process');

function loadConfig(customPath) {
  const cfgPath = customPath || path.resolve(__dirname, 'config.json');
  const raw = fs.readFileSync(cfgPath, 'utf-8');
  return JSON.parse(raw);
}

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--snapshot') {
      args.snapshot = argv[i + 1];
      i++;
    } else if (a === '--output') {
      args.output = argv[i + 1];
      i++;
    }
  }
  return args;
}

function runNodeScript(scriptName, extraArgs) {
  const scriptPath = path.resolve(__dirname, scriptName);
  const result = spawnSync(process.execPath, [scriptPath, ...extraArgs], {
    cwd: __dirname,
    stdio: 'inherit'
  });

  if (result.status !== 0) {
    console.error(`❌ Sub-script ${scriptName} failed with code ${result.status}`);
    process.exit(result.status || 1);
  }
}

(async () => {
  const cfg = loadConfig();
  const args = parseArgs(process.argv.slice(2));

  // Resolve snapshot path: CLI overrides config
  const snapshotRel =
    args.snapshot ||
    cfg.snapshotPath ||
    'source/top_10_hotels_expedia.html';
  const snapshotPath = path.resolve(__dirname, snapshotRel);

  if (!fs.existsSync(snapshotPath)) {
    console.error('❌ Snapshot file not found for Expedia:', snapshotPath);
    process.exit(1);
  }

  // Resolve final output directory for unified HTML variants
  const outputDirArg = args.output || 'data/expedia/html';
  const outputDir = path.resolve(process.cwd(), outputDirArg);

  // Temporary directory where the original scripts write their HTML
  const tempOutputDir = path.resolve(__dirname, 'output_expedia2_unified_complete');

  console.log('🧩 Running Expedia variants - part 1 (style variants)...');
  runNodeScript('generate_unified_variations_part1.js', [
    '--snapshot',
    snapshotPath
  ]);

  console.log('🧩 Running Expedia variants - part 2 (position/order/size/clarity)...');
  runNodeScript('generate_unified_variations_part2.js', [
    '--snapshot',
    snapshotPath
  ]);

  // Copy all generated HTML files into the unified output directory
  fse.ensureDirSync(outputDir);
  if (!fs.existsSync(tempOutputDir)) {
    console.error('❌ Expected temporary output directory not found:', tempOutputDir);
    process.exit(1);
  }

  const files = fs.readdirSync(tempOutputDir).filter(f => f.toLowerCase().endsWith('.html'));
  files.forEach(file => {
    const src = path.join(tempOutputDir, file);
    const dest = path.join(outputDir, file);
    fse.copyFileSync(src, dest);
  });

  console.log(`🎉 Expedia variants generation completed. Copied ${files.length} HTML files to: ${outputDir}`);
})();

