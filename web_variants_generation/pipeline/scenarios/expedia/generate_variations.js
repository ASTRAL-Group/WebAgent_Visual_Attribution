const fs = require('fs');
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

function runNodeScript(scriptName, extraArgs, extraEnv = {}) {
  const scriptPath = path.resolve(__dirname, scriptName);
  const result = spawnSync(process.execPath, [scriptPath, ...extraArgs], {
    cwd: __dirname,
    stdio: 'inherit',
    env: { ...process.env, ...extraEnv }
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

  console.log('🧩 Running Expedia variants - part 1 (style variants)...');
  runNodeScript('generate_unified_variations_part1.js', [
    '--snapshot',
    snapshotPath
  ], { EXPEDIA_OUTPUT_DIR: outputDir });

  console.log('🧩 Running Expedia variants - part 2 (position/order/size/clarity)...');
  runNodeScript('generate_unified_variations_part2.js', [
    '--snapshot',
    snapshotPath
  ], { EXPEDIA_OUTPUT_DIR: outputDir });

  const files = fs.existsSync(outputDir)
    ? fs.readdirSync(outputDir).filter(f => f.toLowerCase().endsWith('.html'))
    : [];
  console.log(`🎉 Expedia variants generation completed. Generated ${files.length} HTML files in: ${outputDir}`);
})();

