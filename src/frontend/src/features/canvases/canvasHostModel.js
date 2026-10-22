import { init, parse } from 'es-module-lexer';

export const CANVAS_ORIGIN = 'https://canvas.invalid';
export const CANVAS_CSP = "default-src 'none'; script-src 'unsafe-inline' data:; style-src 'unsafe-inline' data:; img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'";

function canvasUrl(name, path) {
  return `${CANVAS_ORIGIN}/${encodeURIComponent(name)}/${path.split('/').map(encodeURIComponent).join('/')}`;
}

function resolveCanvasPath(fromPath, specifier) {
  const parts = fromPath.split('/').slice(0, -1);
  for (const segment of specifier.split('/')) {
    if (!segment || segment === '.') continue;
    if (segment === '..') {
      if (!parts.length) throw new Error(`Import escapes canvas root: ${specifier}`);
      parts.pop();
    } else {
      parts.push(segment);
    }
  }
  if (!parts.length || parts.some((part) => !/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(part))) {
    throw new Error(`Invalid canvas import path: ${specifier}`);
  }
  return parts.join('/');
}

function importSpecifier(source, item) {
  if (typeof item.specifier === 'string') return item.specifier;
  const raw = source.slice(item.start, item.end);
  return raw.startsWith('"') || raw.startsWith("'") ? raw.slice(1, -1) : raw;
}

/** Rewrite all literal module import specifiers and report every invalid import. */
export async function rewriteCanvasFiles(name, files, librarySources = {}) {
  await init;
  const rewrittenFiles = {};
  const errors = [];
  for (const [path, source] of Object.entries(files || {})) {
    if (!path.endsWith('.mjs')) continue;
    let imports;
    try {
      [imports] = parse(source);
    } catch (error) {
      errors.push({ path, message: `Unable to parse ${path}: ${error.message}` });
      continue;
    }
    let rewritten = source;
    for (const item of [...imports].reverse()) {
      const specifier = importSpecifier(source, item);
      if (!specifier) {
        errors.push({ path, message: `Unsupported non-literal import in ${path}` });
        continue;
      }
      let replacement;
      try {
        if (specifier.startsWith('./') || specifier.startsWith('../')) {
          const target = resolveCanvasPath(path, specifier);
          if (!target.endsWith('.mjs')) throw new Error(`Only .mjs files are importable: ${specifier}`);
          if (!(target in files)) throw new Error(`Imported file does not exist: ${specifier}`);
          replacement = canvasUrl(name, target);
        } else if (specifier in librarySources) {
          replacement = specifier;
        } else {
          throw new Error(`Unknown bare module specifier: ${specifier}`);
        }
      } catch (error) {
        errors.push({ path, message: error.message });
        continue;
      }
      // Dynamic import records include the quoted specifier; static records do not.
      const dynamic = item.type === 'dynamic';
      rewritten = `${rewritten.slice(0, item.start)}${dynamic ? JSON.stringify(replacement) : replacement}${rewritten.slice(item.end)}`;
    }
    rewrittenFiles[path] = rewritten;
  }
  return { files: rewrittenFiles, errors };
}

export function unicodeBase64(value) {
  const bytes = new TextEncoder().encode(value);
  let binary = '';
  const chunkSize = 0x8000;
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
  }
  return btoa(binary);
}

export function javascriptDataUrl(source) {
  return `data:text/javascript;base64,${unicodeBase64(source)}`;
}

/** Map virtual canvas URLs and vendored library specifiers to self-contained sources. */
export async function buildCanvasImportMap(name, files, librarySources) {
  const rewritten = await rewriteCanvasFiles(name, files, librarySources);
  const imports = {};
  for (const [path, source] of Object.entries(rewritten.files)) {
    imports[canvasUrl(name, path)] = javascriptDataUrl(source);
  }
  await Promise.all(Object.entries(librarySources).map(async ([specifier, load]) => {
    imports[specifier] = javascriptDataUrl(await load());
  }));
  return { imports, errors: rewritten.errors };
}

function jsonForHtml(value) {
  return JSON.stringify(value).replace(/</g, '\\u003c');
}

/** Coerce a host theme into `{mode: 'light'|'dark', tokens: {'--name': value}}`. */
export function normalizeCanvasTheme(theme) {
  const mode = theme?.mode === 'light' ? 'light' : 'dark';
  const tokens = Object.fromEntries(
    Object.entries(theme?.tokens || {}).filter(([key]) => /^--[A-Za-z0-9_-]+$/.test(key)).map(([key, value]) => [key, String(value)]),
  );
  return { mode, tokens };
}

function themeCss(tokens) {
  return `:root{${Object.entries(tokens).map(([key, value]) => `${key}:${value};`).join('')}}`;
}

/**
 * Turn an `artifact` document row (`{id, document: {content, updated_at}}`) into
 * `{path, content, data, updated_at}`. `data` is parsed by extension: JSON, JSONL
 * (array), CSV/TSV (array of header-keyed string records); otherwise the text.
 * Self-contained (nested helpers only): its source is embedded in the iframe bootstrap.
 */
export function artifactFromDocument(item) {
  function parseDelimited(text, separator) {
    // RFC 4180: a field is unquoted, or wholly quoted with "" as an escaped quote.
    // A line with nothing on it is skipped; a line holding "" is an empty record.
    const rows = [];
    let row = [];
    let field = '';
    let state = 'start'; // start | unquoted | quoted | closed
    let touched = false;
    const endField = () => { row.push(field); field = ''; state = 'start'; };
    const endRow = () => {
      endField();
      if (touched) rows.push(row);
      row = [];
      touched = false;
    };
    for (let index = 0; index < text.length; index += 1) {
      const char = text[index];
      if (state === 'quoted') {
        if (char !== '"') field += char;
        else if (text[index + 1] === '"') { field += '"'; index += 1; }
        else state = 'closed';
      } else if (char === separator) {
        touched = true;
        endField();
      } else if (char === '\n' || char === '\r') {
        if (char === '\r' && text[index + 1] === '\n') index += 1;
        endRow();
      } else if (state === 'closed') {
        throw new Error('unexpected character after a closing quote at offset ' + index);
      } else if (char === '"') {
        if (state !== 'start') throw new Error('unexpected quote inside an unquoted field at offset ' + index);
        state = 'quoted';
        touched = true;
      } else {
        field += char;
        state = 'unquoted';
        touched = true;
      }
    }
    if (state === 'quoted') throw new Error('unterminated quoted field');
    if (touched) endRow();
    const [header = [], ...body] = rows;
    return body.map((cells) => Object.fromEntries(header.map((name, column) => [name, cells[column] ?? ''])));
  }
  const path = String(item.id);
  const content = String(item.document?.content ?? '').replace(/^\uFEFF/, '');
  const extension = path.slice(path.lastIndexOf('.')).toLowerCase();
  let data = content;
  try {
    if (extension === '.json') data = JSON.parse(content);
    else if (extension === '.jsonl') data = content.split(/\r?\n/).filter((line) => line.trim()).map((line) => JSON.parse(line));
    else if (extension === '.csv' || extension === '.tsv') data = parseDelimited(content, extension === '.tsv' ? '\t' : ',');
  } catch (error) {
    throw new Error('Artifact ' + path + ' is not valid ' + extension.slice(1) + ': ' + error.message);
  }
  return { path, content, data, updated_at: item.document?.updated_at ?? null };
}

/** Construct the opaque-origin iframe document and its postMessage bridge. */
export async function buildCanvasSrcdoc({ name, entry, files, librarySources, theme }) {
  const { imports, errors } = await buildCanvasImportMap(name, files, librarySources);
  const assets = Object.fromEntries(Object.entries(files || {}).filter(([path]) => !path.endsWith('.mjs')));
  const entryUrl = canvasUrl(name, entry);
  const normalized = normalizeCanvasTheme(theme);
  const { mode } = normalized;
  const importMap = jsonForHtml({ imports });
  const bootstrap = `
const CHANNEL='progenitor-canvas';
const pending=new Map(); let eventHandlers=[];
function request(method,payload={}) { const requestId=crypto.randomUUID(); parent.postMessage({channel:CHANNEL,type:'request',requestId,method,payload},'*'); return new Promise((resolve,reject)=>pending.set(requestId,{resolve,reject})); }
window.addEventListener('message',(event)=>{const message=event.data;if(!message||message.channel!==CHANNEL)return;if(message.type==='reply'){const item=pending.get(message.requestId);if(!item)return;pending.delete(message.requestId);message.error?item.reject(new Error(message.error)):item.resolve(message.payload);}if(message.type==='event'){for(const item of eventHandlers){if(item.patterns.some((pattern)=>matches(pattern,message.event)))item.callback(message.event);}}if(message.type==='theme'){applyTheme(message.theme);}});
function matches(pattern,event){const [type='*',op='*']=pattern.split('.');return (type==='*'||type===event.document_type)&&(op==='*'||op===event.op);}
const assets=${jsonForHtml(assets)};
const themeState=${jsonForHtml(normalized)}; const themeListeners=new Set();
function applyTheme(next){if(!next||(next.mode!=='light'&&next.mode!=='dark'))return;const changed=next.mode!==themeState.mode||JSON.stringify(next.tokens)!==JSON.stringify(themeState.tokens);themeState.mode=next.mode;themeState.tokens={...next.tokens};const root=document.documentElement;root.dataset.theme=next.mode;root.style.colorScheme=next.mode;document.getElementById('progenitor-theme').textContent=':root{'+Object.entries(themeState.tokens).map(([key,value])=>key+':'+value+';').join('')+'}';if(!changed)return;for(const callback of themeListeners){try{callback(host.theme);}catch(error){console.error(error);}}}
const toArtifact=(${artifactFromDocument.toString()});
const host={documents:{list:(type,options={})=>request('documents',{type,...options}).then(({items=[],cursor=null})=>({items,cursor})),get:(type,id)=>request('documents',{type,id}).then(({items=[]})=>items[0]??null)},documentTypes:()=>request('documentTypes'),events:{subscribe:(events,callback)=>{const item={patterns:events,callback};eventHandlers.push(item);request('subscribe',{events:[...new Set(eventHandlers.flatMap((handler)=>handler.patterns))]}).catch(console.error);return()=>{eventHandlers=eventHandlers.filter((candidate)=>candidate!==item);request('subscribe',{events:[...new Set(eventHandlers.flatMap((handler)=>handler.patterns))]}).catch(console.error);};}},data:{get:(key)=>request('data',{op:'get',key}).then(({value})=>value),set:(key,value)=>request('data',{op:'set',key,value}).then(({value:result})=>result),delete:(key)=>request('data',{op:'delete',key}).then(()=>undefined),list:()=>request('data',{op:'list'}).then(({items=[]})=>items)},artifacts:{get:(path)=>request('documents',{type:'artifact',id:path}).then(({items=[]})=>items[0]?toArtifact(items[0]):null),list:async(prefix='')=>{const found=[];let cursor=null;do{const page=await request('documents',{type:'artifact',prefix,limit:500,cursor});for(const item of page.items||[])found.push({path:item.id,updated_at:item.document?.updated_at??null,bytes:new TextEncoder().encode(item.document?.content??'').length});cursor=page.cursor;}while(cursor);return found;},subscribe:(prefix,callback)=>host.events.subscribe(['artifact.*'],(event)=>{if(!String(event.id).startsWith(prefix))return;callback({path:event.id,op:event.op,artifact:event.document?toArtifact({id:event.id,document:event.document}):null});})},asset:(path)=>{if(!(path in assets))throw new Error('Unknown canvas asset: '+path);return assets[path];},theme:{get mode(){return themeState.mode;},get tokens(){return {...themeState.tokens};},get:(name)=>themeState.tokens[name]??getComputedStyle(document.documentElement).getPropertyValue(name).trim(),onChange:(callback)=>{themeListeners.add(callback);return()=>themeListeners.delete(callback);}}};
window.addEventListener('error',(event)=>parent.postMessage({channel:CHANNEL,type:'error',error:{message:event.message,stack:event.error?.stack}},'*'));
window.addEventListener('unhandledrejection',(event)=>parent.postMessage({channel:CHANNEL,type:'error',error:{message:String(event.reason?.message||event.reason),stack:event.reason?.stack}},'*'));
let cleanup; window.addEventListener('beforeunload',()=>cleanup?.());
try { const canvas=await import(${JSON.stringify(entryUrl)}); cleanup=await canvas.default(document.getElementById('root'),host); parent.postMessage({channel:CHANNEL,type:'mounted'},'*'); } catch(error) { parent.postMessage({channel:CHANNEL,type:'error',error:{message:error.message,stack:error.stack}},'*'); }
`;
  return {
    errors,
    srcdoc: `<!doctype html><html data-theme="${mode}" style="color-scheme:${mode}"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${CANVAS_CSP}"><script type="importmap">${importMap}</script><style id="progenitor-theme">${themeCss(normalized.tokens).replace(/</g, '\\3c ')}</style><style>html,body,#root{width:100%;height:100%;margin:0;overflow:hidden}</style></head><body><div id="root"></div><script type="module">${bootstrap.replace(/<\/script/gi, '<\\/script')}</script></body></html>`,
  };
}
