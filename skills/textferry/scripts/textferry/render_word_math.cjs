// SPDX-License-Identifier: AGPL-3.0-only
// One JSON request on stdin, one JSON response on stdout. No network services.
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

function dependency(name) {
  for (const directory of (process.env.TEXTFERRY_NODE_MODULES || '').split(path.delimiter).filter(Boolean)) {
    try { return require(require.resolve(name, {paths: [path.dirname(path.resolve(directory))]})); }
    catch (error) { if (error.code !== 'MODULE_NOT_FOUND') throw error; }
  }
  return require(name);
}

async function main() {
  const request = JSON.parse(fs.readFileSync(0, 'utf8'));
  const {mathjax} = dependency('mathjax-full/js/mathjax.js');
  const {TeX} = dependency('mathjax-full/js/input/tex.js');
  const {SVG} = dependency('mathjax-full/js/output/svg.js');
  const {liteAdaptor} = dependency('mathjax-full/js/adaptors/liteAdaptor.js');
  const {RegisterHTMLHandler} = dependency('mathjax-full/js/handlers/html.js');
  const {AllPackages} = dependency('mathjax-full/js/input/tex/AllPackages.js');
  const sharp = dependency('sharp');
  const renderer = `mathjax-full@${dependency('mathjax-full/package.json').version}+sharp@${sharp.versions.sharp}`;
  const adaptor = liteAdaptor();
  RegisterHTMLHandler(adaptor);
  const input = new TeX({
    packages: AllPackages.filter(name => !['noerrors', 'noundefined', 'html'].includes(name)),
    maxBuffer: 1024 * 1024,
    formatError: (_jax, error) => { throw error; },
  });
  const doc = mathjax.document('', {InputJax: input, OutputJax: new SVG({fontCache: 'none'})});
  const directory = fs.realpathSync(request.directory);
  const emPt = 11, dpi = 400, results = [];
  for (const item of request.equations) {
    const key = crypto.createHash('sha256').update(JSON.stringify([2, renderer, emPt, dpi, item.tex, item.display])).digest('hex');
    const output = path.join(directory, `${key}.png`);
    const metadata = path.join(directory, `${key}.json`);
    let record;
    if (fs.existsSync(output) && fs.existsSync(metadata)) {
      record = JSON.parse(fs.readFileSync(metadata, 'utf8'));
      if (record.key !== key || record.tex !== item.tex || record.display !== item.display) throw new Error('Formula cache metadata does not match its key');
      results.push({...record, id: item.id, path: output, cache_hit: true});
      continue;
    }
    // Reset equation labels so independent source pages can reuse printed tags.
    input.parseOptions.tags.reset();
    // Tagged MathJax equations use a full-width wrapper without a viewBox.
    // Render numbers at their original line position, retaining original TeX.
    const renderTex = item.tex.replace(/\\tag(\*)?\s*\{([^{}]*)\}/g,
      (_match, star, label) => `\\qquad\\text{${star ? label : '(' + label + ')'}}`);
    const container = doc.convert(renderTex, {display: item.display});
    const svgNode = adaptor.firstChild(container);
    let svg = adaptor.outerHTML(svgNode);
    if (svg.includes('data-mml-node="merror"') || svg.includes('data-mjx-error=')) throw new Error(`Invalid TeX: ${item.tex.slice(0, 100)}`);
    const box = adaptor.getAttribute(svgNode, 'viewBox').trim().split(/\s+/).map(Number);
    if (box.length !== 4 || !box.every(Number.isFinite) || box[2] <= 0 || box[3] <= 0) throw new Error('Formula has no visible dimensions');
    const widthPt = box[2] / 1000 * emPt, heightPt = box[3] / 1000 * emPt;
    const pixelWidth = Math.max(1, Math.ceil(widthPt / 72 * dpi));
    const pixelHeight = Math.max(1, Math.ceil(heightPt / 72 * dpi));
    // Only replace the root dimensions: internal rect dimensions draw rules.
    svg = svg.replace(/^<svg\b[^>]*>/, root => root.replace(/\s(?:width|height)="[^"]*"/g, '')
      .replace('<svg ', `<svg width="${pixelWidth}px" height="${pixelHeight}px" `))
      .replace(/currentColor/g, '#000000');
    const temporary = output + `.tmp-${process.pid}`;
    await sharp(Buffer.from(svg), {density: 72}).png().withMetadata({density: dpi}).toFile(temporary);
    fs.renameSync(temporary, output);
    const naturalWidth = widthPt / 72 * 2.54, naturalHeight = heightPt / 72 * 2.54;
    const scale = Math.min(1, 16.4 / naturalWidth, 18 / naturalHeight);
    record = {key, renderer, tex: item.tex, render_tex: renderTex, display: item.display, dpi,
      width_cm: Math.min(16.4, naturalWidth * scale), height_cm: Math.min(18, naturalHeight * scale), scale};
    fs.writeFileSync(metadata, JSON.stringify(record, null, 2) + '\n', {flag: 'wx'});
    results.push({...record, id: item.id, path: output, cache_hit: false});
  }
  process.stdout.write(JSON.stringify({renderer, equations: results}));
}

main().catch(error => { process.stderr.write(String(error.stack || error) + '\n'); process.exitCode = 1; });
