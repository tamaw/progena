import { tokenize } from '../../../lib/restrictedExpression';
import { tokenizer } from 'acorn';
import { predecessorsOf } from './graphTopology';

// What identifier/dotted-attribute chain, if any, ends right at the caret --
// tokenizes the text up to `caret` with the exact tokenizer
// `checkExpressionSyntax` uses (see lib/restrictedExpression.js), so a caret
// sitting inside a string or number literal correctly gets no suggestions:
// an unterminated-string tokenize error, or the caret landing right after a
// closed str/num token, both mean "not actually typing an identifier here".
// Anywhere else -- nothing typed yet, right after whitespace, right after an
// operator -- returns an EMPTY query rather than null, so the menu can open
// the moment the field is focused (the caller gates that on focus, not on
// this function) and show every candidate before narrowing as the author
// types. A DANGLING dot ("classify_intent.") is its own case: the query
// includes the trailing dot, which narrows suggestions to that node's own
// properties via a plain substring match (nothing else contains "x.") without
// needing any special-cased "drill into a specific node" branch. Backward-
// only, same convention as chatInputModel.js's mention detector -- whatever's
// already typed past the caret on the same token is left alone.
export function getExpressionAutocompleteContext(text, caret) {
  const source = typeof text === 'string' ? text : '';
  const position = Math.min(Number.isFinite(caret) ? caret : source.length, source.length);
  const tokenized = tokenize(source.slice(0, position));
  if (tokenized.error) return null;
  const tokens = tokenized.tokens;
  const lastIndex = tokens.length - 2; // skip the trailing synthetic eof token
  if (lastIndex < 0) return { query: '', tokenStart: position, tokenEnd: position };
  const last = tokens[lastIndex];
  if (last.end !== position) return { query: '', tokenStart: position, tokenEnd: position };
  if (last.type === 'str' || last.type === 'num') return null;
  const danglingDot = last.type === 'op' && last.value === '.';
  if (last.type !== 'name' && !danglingDot) {
    return { query: '', tokenStart: position, tokenEnd: position };
  }
  let start = last.start;
  let i = lastIndex - 1;
  let expectName = danglingDot;
  while (i >= 0) {
    const tok = tokens[i];
    if (expectName) {
      if (tok.type !== 'name' || tok.end !== start) break;
    } else {
      if (tok.type !== 'op' || tok.value !== '.' || tok.end !== start) break;
    }
    start = tok.start;
    expectName = !expectName;
    i -= 1;
  }
  return { query: source.slice(start, position), tokenStart: start, tokenEnd: position };
}

// Tokenize JavaScript rather than reusing the restricted Python expression
// parser: strings, comments, regexes and template text are not references.
export function getJavascriptAutocompleteContext(text, caret) {
  const position = Math.max(0, Math.min(caret, text.length));
  const tokens = [];
  const comments = [];
  try {
    const lexer = tokenizer(text.slice(0, position), {
      ecmaVersion: 'latest',
      onComment: (_block, _text, start, end) => comments.push({ start, end }),
    });
    for (let token = lexer.getToken(); token.type.label !== 'eof'; token = lexer.getToken()) {
      tokens.push(token);
    }
  } catch {
    return null;
  }
  if (comments.some(({ start, end }) => position > start && position <= end)) return null;
  // Find an input-rooted member expression ending at the caret. An unfinished
  // bracket is allowed so choosing a candidate can insert the complete access.
  for (let index = tokens.length - 1; index >= 0; index -= 1) {
    const token = tokens[index];
    if (token.type.label !== 'name' || token.value !== 'input') continue;
    if (tokens[index - 1]?.type.label === '.' || tokens[index - 1]?.type.label === '?.') continue;
    const suffix = tokens.slice(index + 1);
    // Require explicit member access: bare prefixes can be declaration or
    // parameter bindings, where inserting a member expression corrupts code.
    if (!suffix.length) continue;
    let valid = true;
    let pendingBracket = false;
    let query = token.value;
    for (let i = 0; i < suffix.length;) {
      const label = suffix[i].type.label;
      if (label === '.') {
        i += 1;
        query += '.';
        if (i < suffix.length) {
          if (suffix[i].type.label !== 'name' && !suffix[i].type.keyword) valid = false;
          query += suffix[i++].value;
        }
      } else if (label === '[') {
        i += 1;
        pendingBracket = true;
        if (i < suffix.length) {
          if (suffix[i].type.label !== 'string') valid = false;
          query += `[${JSON.stringify(suffix[i++].value)}]`;
          if (i < suffix.length) {
            if (suffix[i++].type.label !== ']') valid = false;
            pendingBracket = false;
          }
        }
      } else {
        valid = false;
        break;
      }
    }
    if (!valid || tokens.at(-1)?.end !== position) return null;
    const tail = text.slice(position).match(/^[\w$]*/)?.[0] || '';
    const remaining = text.slice(position);
    const closingBracket = pendingBracket ? remaining.match(/^\s*\]/)?.[0].length || 0 : 0;
    if (pendingBracket && !closingBracket && remaining.trim()) return null;
    return { query: query.replace(/\.$/, ''), tokenStart: token.start, tokenEnd: position + tail.length + closingBracket };
  }
  return null;
}

export function javascriptStatePathSuggestions(suggestions) {
  return suggestions.map((item) => ({
    ...item,
    path: `input${item.segments.map((segment) => (
      /^[A-Za-z_$][\w$]*$/.test(segment) ? `.${segment}` : `[${JSON.stringify(segment)}]`
    )).join('')}`,
  }));
}

// `eval`/`condition` nodes resolve named inputs from dotted `data` paths before
// evaluation (config.inputs, an object). Edited as one "name: path" per line so
// authors don't have to hand-write JSON for a small map.
export function inputsToText(inputs) {
  return Object.entries(inputs || {}).map(([key, path]) => `${key}: ${path}`).join('\n');
}

export function textToInputs(text) {
  const result = {};
  text.split('\n').forEach((line) => {
    const separator = line.indexOf(':');
    if (separator === -1) return;
    const key = line.slice(0, separator).trim();
    const path = line.slice(separator + 1).trim();
    if (key) result[key] = path;
  });
  return result;
}

// `startsWith` matches first, `includes` matches after, both case-
// insensitive against the dotted path text (not the display source/label).
// A dangling-dot query ("classify_intent.") also keeps the bare object
// itself in the list even though it doesn't literally contain the trailing
// dot -- accepting it undoes the dot (replaces the whole "classify_intent."
// span with just "classify_intent"), which matters most for a node with no
// declared response_schema: `prompt.` has no `.property` entries to narrow
// to at all, so without this the menu would just show nothing once you
// typed the dot, even though `prompt` itself is a perfectly good state
// reference on its own.
export function filterStatePathSuggestions(suggestions, query) {
  const q = (query || '').toLowerCase();
  const bareMatch = q.endsWith('.') ? q.slice(0, -1) : null;
  return suggestions
    .filter((item) => {
      const path = item.path.toLowerCase();
      return path.includes(q) || path === bareMatch;
    })
    .sort((a, b) => {
      const aStarts = a.path.toLowerCase().startsWith(q) ? 0 : 1;
      const bStarts = b.path.toLowerCase().startsWith(q) ? 0 : 1;
      return aStarts - bStarts;
    });
}

// `classify`/`prompt`/`agent` nodes' response_schema is plain JSON Schema, but
// authoring one by hand for a flat set of output fields is needless ceremony.
// Edited as one "name: type[ #description]" per line -- the same "name: value"
// convention as inputsToText/textToInputs above -- and converted to a strict
// JSON Schema object (every field required, additionalProperties: false, since
// that's what with_structured_output(method="json_schema", strict=True) needs).
// Schemas this can't represent (anything not a flat object of primitives --
// $ref indirection, nested objects, oneOf, etc.) round-trip to an empty list;
// authors needing that still reach it via the graph-level "Edit as JSON" dock.
const SCHEMA_FIELD_TYPE_ALIASES = { bool: 'boolean', str: 'string', int: 'integer', num: 'number' };
const SCHEMA_FIELD_PRIMITIVE_TYPES = ['string', 'number', 'integer', 'boolean'];

function parseSchemaFieldType(raw) {
  const trimmed = raw.trim();
  const enumMatch = trimmed.match(/^enum\((.*)\)$/i);
  if (enumMatch) {
    return { type: 'string', enum: enumMatch[1].split(',').map((value) => value.trim()).filter(Boolean) };
  }
  const arrayMatch = trimmed.match(/^(\w+)\[\]$/);
  if (arrayMatch) {
    const itemType = SCHEMA_FIELD_TYPE_ALIASES[arrayMatch[1].toLowerCase()] || arrayMatch[1].toLowerCase();
    return { type: 'array', items: { type: SCHEMA_FIELD_PRIMITIVE_TYPES.includes(itemType) ? itemType : 'string' } };
  }
  const normalized = SCHEMA_FIELD_TYPE_ALIASES[trimmed.toLowerCase()] || trimmed.toLowerCase();
  return { type: SCHEMA_FIELD_PRIMITIVE_TYPES.includes(normalized) ? normalized : 'string' };
}

export function schemaFieldsToText(schema) {
  const properties = schema?.properties;
  if (!properties || typeof properties !== 'object') return '';
  return Object.entries(properties).map(([name, def]) => {
    let typeText;
    if (Array.isArray(def?.enum)) typeText = `enum(${def.enum.join(', ')})`;
    else if (def?.type === 'array') typeText = `${def.items?.type || 'string'}[]`;
    else typeText = def?.type || 'string';
    return def?.description ? `${name}: ${typeText} # ${def.description}` : `${name}: ${typeText}`;
  }).join('\n');
}

export function textToSchemaFields(text) {
  const properties = {};
  const required = [];
  text.split('\n').forEach((line) => {
    const separator = line.indexOf(':');
    if (separator === -1) return;
    const name = line.slice(0, separator).trim();
    if (!name) return;
    const rest = line.slice(separator + 1).trim();
    const hashIndex = rest.indexOf('#');
    const typeRaw = (hashIndex === -1 ? rest : rest.slice(0, hashIndex)).trim();
    const description = hashIndex === -1 ? '' : rest.slice(hashIndex + 1).trim();
    const def = parseSchemaFieldType(typeRaw || 'string');
    properties[name] = description ? { ...def, description } : def;
    required.push(name);
  });
  return { type: 'object', properties, required, additionalProperties: false };
}

// Whether every line is a finished "name: type" declaration that
// parseSchemaFieldType reads as written (unknown/partial types such as
// `inte` or an unclosed `enum(` would silently become `string`).
export function isCompleteSchemaFieldText(text) {
  return text.split('\n').every((line) => {
    if (!line.trim()) return true;
    const separator = line.indexOf(':');
    if (separator <= 0 || !line.slice(0, separator).trim()) return false;
    const type = line.slice(separator + 1).split('#', 1)[0].trim();
    const enumMatch = type.match(/^enum\((.*)\)$/i);
    if (enumMatch) return enumMatch[1].split(',').every((value) => value.trim());
    const base = (type.match(/^(\w+)\[\]$/)?.[1] ?? type).toLowerCase();
    return SCHEMA_FIELD_PRIMITIVE_TYPES.includes(SCHEMA_FIELD_TYPE_ALIASES[base] || base);
  });
}

// The candidate state paths a node at `nodeId` could reference -- Expression
// (eval/condition/switch/loop_guard) resolves these as direct name/attribute
// access; Inputs (agent) still binds them under an author-chosen local alias.
// Either way: the routine's own declared invocation inputs, the two
// always-seeded keys, and every predecessor's output key -- plus, for a
// predecessor carrying a `response_schema` (Select/classify always; prompt/
// agent when authored with one), a `<output_key>.<property>` entry per
// schema field, since both consumers already walk nested dicts
// (`src/progena/routines/state.py`'s `resolve_data_path`/`_resolve_inputs`, and
// `expression.py`'s `ast.Attribute` handling) with no synthetic flattening
// needed. `eval`/`switch`/`condition` predecessors offer only their bare key
// -- their result has no declared shape to drill into.
export function buildStatePathSuggestions(nodeId, nodes, edges, inputSchema) {
  const nodesById = new Map((nodes || []).map((node) => [node.id, node]));
  const seen = new Set();
  const suggestions = [];
  const add = (path, entry, segments = [path]) => {
    if (seen.has(path)) return;
    seen.add(path);
    suggestions.push({ path, segments, name: path.includes('.') ? path.slice(path.lastIndexOf('.') + 1) : path, ...entry });
  };
  add('request', { source: 'Seeded', hint: 'the routine invocation request' });
  add('context', { source: 'Seeded', hint: 'accumulated context' });
  Object.entries(inputSchema?.properties || {}).forEach(([name, def]) => add(name, { source: 'Routine input', hint: def?.type }));
  predecessorsOf(nodeId, edges).forEach((id) => {
    const node = nodesById.get(id);
    if (!node) return;
    const source = node.label || node.id;
    const outputKey = node.output_key || node.id;
    add(outputKey, { source, kind: node.kind });
    if (node.kind === 'agent' && node.config?.execution === 'background') {
      ['task_id', 'thread_id', 'run_id', 'agent_name', 'status'].forEach((property) => {
        add(`${outputKey}.${property}`, { source, kind: node.kind, hint: 'string' }, [outputKey, property]);
      });
      return;
    }
    const properties = node.config?.response_schema?.properties;
    if (properties && typeof properties === 'object') {
      Object.keys(properties).forEach((prop) => add(`${outputKey}.${prop}`, { source, kind: node.kind, hint: properties[prop]?.type }, [outputKey, prop]));
    }
  });
  return suggestions;
}
