import { init, parse } from 'es-module-lexer';

export const MODULE_ORIGIN = 'https://module.invalid';
export const MODULE_CSP = "default-src 'none'; script-src 'unsafe-inline' data:; style-src 'unsafe-inline' data:; img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'";

function moduleUrl(name, path) {
  return `${MODULE_ORIGIN}/${encodeURIComponent(name)}/${path.split('/').map(encodeURIComponent).join('/')}`;
}

function resolveModulePath(fromPath, specifier) {
  const parts = fromPath.split('/').slice(0, -1);
  for (const segment of specifier.split('/')) {
    if (!segment || segment === '.') continue;
    if (segment === '..') {
      if (!parts.length) throw new Error(`Import escapes module root: ${specifier}`);
      parts.pop();
    } else {
      parts.push(segment);
    }
  }
  if (!parts.length || parts.some((part) => !/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(part))) {
    throw new Error(`Invalid module import path: ${specifier}`);
  }
  return parts.join('/');
}

function importSpecifier(source, item) {
  if (typeof item.specifier === 'string') return item.specifier;
  const raw = source.slice(item.start, item.end);
  return raw.startsWith('"') || raw.startsWith("'") ? raw.slice(1, -1) : raw;
}

/** Rewrite all literal module import specifiers and report every invalid import. */
export async function rewriteModuleFiles(name, files, librarySources = {}) {
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
          const target = resolveModulePath(path, specifier);
          if (!target.endsWith('.mjs')) throw new Error(`Only .mjs files are importable: ${specifier}`);
          if (!(target in files)) throw new Error(`Imported file does not exist: ${specifier}`);
          replacement = moduleUrl(name, target);
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

/** Map virtual module URLs and vendored library specifiers to self-contained sources. */
export async function buildModuleImportMap(name, files, librarySources) {
  const rewritten = await rewriteModuleFiles(name, files, librarySources);
  const imports = {};
  for (const [path, source] of Object.entries(rewritten.files)) {
    imports[moduleUrl(name, path)] = javascriptDataUrl(source);
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
export function normalizeModuleTheme(theme) {
  const mode = theme?.mode === 'light' ? 'light' : 'dark';
  const tokens = Object.fromEntries(
    Object.entries(theme?.tokens || {}).filter(([key]) => /^--[A-Za-z0-9_-]+$/.test(key)).map(([key, value]) => [key, String(value)]),
  );
  return { mode, tokens };
}

function themeCss(tokens) {
  return `:root{${Object.entries(tokens).map(([key, value]) => `${key}:${value};`).join('')}}`;
}

/** Construct the opaque-origin iframe document and its postMessage bridge. */
export async function buildModuleSrcdoc({ name, entry, files, librarySources, theme }) {
  const { imports, errors } = await buildModuleImportMap(name, files, librarySources);
  const assets = Object.fromEntries(Object.entries(files || {}).filter(([path]) => !path.endsWith('.mjs')));
  const entryUrl = moduleUrl(name, entry);
  const normalized = normalizeModuleTheme(theme);
  const { mode } = normalized;
  const importMap = jsonForHtml({ imports });
  const bootstrap = `
const CHANNEL='progenitor-module';
const pending=new Map(); let eventHandlers=[];
function request(method,payload={}) { const requestId=crypto.randomUUID(); parent.postMessage({channel:CHANNEL,type:'request',requestId,method,payload},'*'); return new Promise((resolve,reject)=>pending.set(requestId,{resolve,reject})); }
window.addEventListener('message',(event)=>{const message=event.data;if(!message||message.channel!==CHANNEL)return;if(message.type==='reply'){const item=pending.get(message.requestId);if(!item)return;pending.delete(message.requestId);message.error?item.reject(new Error(message.error)):item.resolve(message.payload);}if(message.type==='event'){for(const item of eventHandlers){if(item.patterns.some((pattern)=>matches(pattern,message.event)))item.callback(message.event);}}if(message.type==='theme'){applyTheme(message.theme);}});
function matches(pattern,event){const [type='*',op='*']=pattern.split('.');return (type==='*'||type===event.document_type)&&(op==='*'||op===event.op);}
const assets=${jsonForHtml(assets)};
const themeState=${jsonForHtml(normalized)}; const themeListeners=new Set();
function applyTheme(next){if(!next||(next.mode!=='light'&&next.mode!=='dark'))return;const changed=next.mode!==themeState.mode||JSON.stringify(next.tokens)!==JSON.stringify(themeState.tokens);themeState.mode=next.mode;themeState.tokens={...next.tokens};const root=document.documentElement;root.dataset.theme=next.mode;root.style.colorScheme=next.mode;document.getElementById('progenitor-theme').textContent=':root{'+Object.entries(themeState.tokens).map(([key,value])=>key+':'+value+';').join('')+'}';if(!changed)return;for(const callback of themeListeners){try{callback(host.theme);}catch(error){console.error(error);}}}
const host={documents:{list:(type,options={})=>request('documents',{type,...options}).then(({items=[],cursor=null})=>({items,cursor})),get:(type,id)=>request('documents',{type,id}).then(({items=[]})=>items[0]??null)},documentTypes:()=>request('documentTypes'),events:{subscribe:(events,callback)=>{const item={patterns:events,callback};eventHandlers.push(item);request('subscribe',{events:[...new Set(eventHandlers.flatMap((handler)=>handler.patterns))]}).catch(console.error);return()=>{eventHandlers=eventHandlers.filter((candidate)=>candidate!==item);request('subscribe',{events:[...new Set(eventHandlers.flatMap((handler)=>handler.patterns))]}).catch(console.error);};}},data:{get:(key)=>request('data',{op:'get',key}).then(({value})=>value),set:(key,value)=>request('data',{op:'set',key,value}).then(({value:result})=>result),delete:(key)=>request('data',{op:'delete',key}).then(()=>undefined),list:()=>request('data',{op:'list'}).then(({items=[]})=>items)},asset:(path)=>{if(!(path in assets))throw new Error('Unknown module asset: '+path);return assets[path];},theme:{get mode(){return themeState.mode;},get tokens(){return {...themeState.tokens};},get:(name)=>themeState.tokens[name]??getComputedStyle(document.documentElement).getPropertyValue(name).trim(),onChange:(callback)=>{themeListeners.add(callback);return()=>themeListeners.delete(callback);}}};
window.addEventListener('error',(event)=>parent.postMessage({channel:CHANNEL,type:'error',error:{message:event.message,stack:event.error?.stack}},'*'));
window.addEventListener('unhandledrejection',(event)=>parent.postMessage({channel:CHANNEL,type:'error',error:{message:String(event.reason?.message||event.reason),stack:event.reason?.stack}},'*'));
let cleanup; window.addEventListener('beforeunload',()=>cleanup?.());
try { const module=await import(${JSON.stringify(entryUrl)}); cleanup=await module.default(document.getElementById('root'),host); parent.postMessage({channel:CHANNEL,type:'mounted'},'*'); } catch(error) { parent.postMessage({channel:CHANNEL,type:'error',error:{message:error.message,stack:error.stack}},'*'); }
`;
  return {
    errors,
    srcdoc: `<!doctype html><html data-theme="${mode}" style="color-scheme:${mode}"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${MODULE_CSP}"><script type="importmap">${importMap}</script><style id="progenitor-theme">${themeCss(normalized.tokens).replace(/</g, '\\3c ')}</style><style>html,body,#root{width:100%;height:100%;margin:0;overflow:hidden}</style></head><body><div id="root"></div><script type="module">${bootstrap.replace(/<\/script/gi, '<\\/script')}</script></body></html>`,
  };
}
