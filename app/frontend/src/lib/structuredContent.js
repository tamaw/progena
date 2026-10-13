/**
 * Structured-tag parser for Markdown document bodies.
 *
 * Parses arbitrary nested XML-like tags with attributes into a node tree.
 * Everything inside fenced code blocks (```) or inline backticks (`) is
 * left as literal text.  Malformed / unclosed constructs are emitted as
 * literal text nodes so they can never be executed as HTML.
 *
 * Offsets are UTF-16 code-unit indices (standard JavaScript string indices).
 */

/**
 * @typedef {Object} TextNode
 * @property {'text'} type
 * @property {string} content
 * @property {number} start
 * @property {number} end
 */

/**
 * @typedef {Object} SectionNode
 * @property {'section'} type
 * @property {string} tag
 * @property {Record<string,string>} attributes
 * @property {number} start
 * @property {number} end
 * @property {number} openEnd   // index after the opening '>'
 * @property {number} closeStart // index of the closing '<'
 * @property {Array<TextNode|SectionNode>} children
 */

const VOID_TAGS = new Set([
  'area','base','br','col','embed','hr','img','input',
  'link','meta','param','source','track','wbr',
]);

/**
 * Parse a source string into an array of top-level nodes.
 * @param {string} source
 * @returns {Array<TextNode|SectionNode>}
 */
export function parseStructuredContent(source) {
  const parser = new Parser(String(source ?? ''));
  return parser.parseDocument();
}

class Parser {
  constructor(source) {
    this.source = source;
    this.pos = 0;
    this.len = source.length;
  }

  parseDocument() {
    const nodes = [];
    while (this.pos < this.len) {
      const node = this.parseTopLevelNode();
      if (node) nodes.push(node);
    }
    return mergeTextNodes(nodes);
  }

  parseTopLevelNode() {
    if (this.pos >= this.len) return null;

    // Code fence always wins
    if (this.peek(3) === '```' || this.peek(3) === '~~~') {
      return this.consumeCodeFence();
    }
    // Inline backtick
    if (this.peek(1) === '`') {
      return this.consumeInlineCode();
    }

    if (this.peek(1) === '<') {
      const tagStart = this.pos;
      const parsed = this.tryParseTag();
      if (parsed) {
        if (parsed.kind === 'open') {
          if (VOID_TAGS.has(parsed.name)) {
            return this.emitSelfClosing(tagStart, parsed);
          }
          const section = this.parseSection(tagStart, parsed);
          // If parseSection returns a text node, it means the tag was unclosed
          // and we fell back to text.
          return section;
        }
        if (parsed.kind === 'self') {
          return this.emitSelfClosing(tagStart, parsed);
        }
        if (parsed.kind === 'close') {
          // Unexpected close tag at top level → literal text
          return this.emitText(tagStart, this.pos);
        }
      }
      // Not a valid tag → consume one char as text
      this.pos++;
      return this.emitText(tagStart, this.pos);
    }

    return this.consumePlainText();
  }

  /**
   * Parse a section starting with an already-consumed open tag.
   * Returns a SectionNode on success, or a TextNode on failure (unclosed).
   */
  parseSection(start, openTag) {
    // Stack of { tag, start, openTag, children }
    const stack = [{ tag: openTag.name, start, openTag, children: [] }];

    while (this.pos < this.len) {
      // Code fences and inline code are always literal text
      if (this.peek(3) === '```' || this.peek(3) === '~~~') {
        stack[stack.length - 1].children.push(this.consumeCodeFence());
        continue;
      }
      if (this.peek(1) === '`') {
        stack[stack.length - 1].children.push(this.consumeInlineCode());
        continue;
      }

      if (this.peek(1) === '<') {
        const innerTagStart = this.pos;
        const parsed = this.tryParseTag();
        if (!parsed) {
          this.pos++;
          stack[stack.length - 1].children.push(this.emitText(innerTagStart, this.pos));
          continue;
        }

        if (parsed.kind === 'open') {
          if (VOID_TAGS.has(parsed.name)) {
            stack[stack.length - 1].children.push(this.emitSelfClosing(innerTagStart, parsed));
          } else {
            stack.push({ tag: parsed.name, start: innerTagStart, openTag: parsed, children: [] });
          }
          continue;
        }

        if (parsed.kind === 'self') {
          stack[stack.length - 1].children.push(this.emitSelfClosing(innerTagStart, parsed));
          continue;
        }

        if (parsed.kind === 'close') {
          const top = stack[stack.length - 1];
          if (top.tag === parsed.name) {
            const section = this.buildSection(
              top.start,
              top.openTag,
              innerTagStart,
              this.pos,
              top.children
            );
            stack.pop();
            if (stack.length === 0) {
              return section;
            }
            stack[stack.length - 1].children.push(section);
            continue;
          }
          // Mismatched close tag → literal text
          stack[stack.length - 1].children.push(this.emitText(innerTagStart, this.pos));
          continue;
        }
      }

      // Plain text inside current section
      const textStart = this.pos;
      while (this.pos < this.len) {
        const ch = this.source[this.pos];
        if (ch === '<' || this.peek(3) === '```' || this.peek(3) === '~~~' || ch === '`') break;
        this.pos++;
      }
      if (this.pos > textStart) {
        stack[stack.length - 1].children.push(this.emitText(textStart, this.pos));
      }
    }

    // Ran out of source with unclosed tag(s) → emit everything from the
    // original start to current position as plain text.
    return this.emitText(start, this.pos);
  }

  buildSection(start, openTag, closeStart, end, children) {
    return {
      type: 'section',
      tag: openTag.name,
      attributes: openTag.attributes,
      start,
      end,
      openEnd: openTag.end,
      closeStart,
      children: mergeTextNodes(children),
    };
  }

  emitSelfClosing(start, parsed) {
    return {
      type: 'section',
      tag: parsed.name,
      attributes: parsed.attributes,
      start,
      end: parsed.end,
      openEnd: parsed.end,
      closeStart: parsed.end,
      children: [],
    };
  }

  emitText(start, end) {
    return {
      type: 'text',
      content: this.source.slice(start, end),
      start,
      end,
    };
  }

  consumePlainText() {
    const start = this.pos;
    while (this.pos < this.len) {
      const ch = this.source[this.pos];
      if (ch === '<' || this.peek(3) === '```' || this.peek(3) === '~~~' || ch === '`') break;
      this.pos++;
    }
    return this.emitText(start, this.pos);
  }

  consumeCodeFence() {
    const start = this.pos;
    const marker = this.source[this.pos];
    this.pos += 3;
    while (this.source[this.pos] === marker) this.pos++;
    const size = this.pos - start;
    // The fence opening line (including its info string) is literal.
    while (this.pos < this.len && this.source[this.pos] !== '\n') this.pos++;
    if (this.pos < this.len) this.pos++;
    while (this.pos < this.len) {
      if (this.source[this.pos] === marker) {
        let end = this.pos;
        while (this.source[end] === marker) end++;
        if (end - this.pos >= size && (this.source[end] === '\n' || this.source[end] === '<' || end === this.len)) {
          this.pos = end;
          break;
        }
      }
      this.pos++;
    }
    return this.emitText(start, this.pos);
  }

  consumeInlineCode() {
    const start = this.pos;
    this.pos++; // consume `
    while (this.pos < this.len && this.source[this.pos] !== '`') {
      this.pos++;
    }
    if (this.pos < this.len) this.pos++; // consume closing `
    return this.emitText(start, this.pos);
  }

  /**
   * Attempt to parse a tag at current position.
   * On success, advances `this.pos` past the tag and returns descriptor.
   * On failure, leaves `this.pos` unchanged and returns null.
   */
  tryParseTag() {
    const savedPos = this.pos;
    if (this.source[this.pos] !== '<') return null;
    this.pos++;

    const isClose = this.source[this.pos] === '/';
    if (isClose) this.pos++;

    // Tag name
    const nameStart = this.pos;
    if (this.pos >= this.len || !isTagNameChar(this.source[this.pos], true)) {
      this.pos = savedPos;
      return null;
    }
    while (this.pos < this.len && isTagNameChar(this.source[this.pos], false)) {
      this.pos++;
    }
    const name = this.source.slice(nameStart, this.pos);

    // Attributes (only for open/self-closing)
    let attributes = {};
    if (!isClose) {
      const attrResult = this.parseAttributes();
      if (!attrResult.ok) {
        this.pos = savedPos;
        return null;
      }
      attributes = attrResult.attrs;
    }

    // Check for self-closing or normal close
    let selfClosing = false;
    if (!isClose && this.source[this.pos] === '/' && this.source[this.pos + 1] === '>') {
      selfClosing = true;
      this.pos += 2;
    } else if (this.pos < this.len && this.source[this.pos] === '>') {
      this.pos++;
    } else {
      this.pos = savedPos;
      return null;
    }

    if (isClose) {
      return { kind: 'close', name, end: this.pos };
    }
    if (selfClosing) {
      return { kind: 'self', name, attributes, end: this.pos };
    }
    return { kind: 'open', name, attributes, end: this.pos };
  }

  parseAttributes() {
    const attrs = {};
    while (this.pos < this.len) {
      // Skip whitespace
      while (this.pos < this.len && /\s/.test(this.source[this.pos])) this.pos++;
      if (this.pos >= this.len) return { ok: false };
      if (this.source[this.pos] === '>' || this.source[this.pos] === '/') return { ok: true, attrs };

      // Attribute name
      const nameStart = this.pos;
      if (!isAttrNameChar(this.source[this.pos], true)) return { ok: false };
      this.pos++;
      while (this.pos < this.len && isAttrNameChar(this.source[this.pos], false)) this.pos++;
      const attrName = this.source.slice(nameStart, this.pos);

      // Optional =value
      let attrValue = '';
      while (this.pos < this.len && /\s/.test(this.source[this.pos])) this.pos++;
      if (this.pos < this.len && this.source[this.pos] === '=') {
        this.pos++;
        while (this.pos < this.len && /\s/.test(this.source[this.pos])) this.pos++;
        const quote = this.source[this.pos];
        if (quote === '"' || quote === "'") {
          this.pos++;
          const valueStart = this.pos;
          while (this.pos < this.len && this.source[this.pos] !== quote) this.pos++;
          attrValue = this.source.slice(valueStart, this.pos);
          if (this.pos < this.len) this.pos++; // consume closing quote
        } else {
          const valueStart = this.pos;
          while (this.pos < this.len && !/[\s>\/]/.test(this.source[this.pos])) this.pos++;
          attrValue = this.source.slice(valueStart, this.pos);
        }
      }
      // else boolean attribute with empty value
      attrs[attrName] = attrValue;
    }
    return { ok: false };
  }

  peek(n) {
    return this.source.slice(this.pos, this.pos + n);
  }
}

function isTagNameChar(ch, first) {
  if (!ch) return false;
  const code = ch.charCodeAt(0);
  if (first) {
    return (code >= 65 && code <= 90) || (code >= 97 && code <= 122);
  }
  return (code >= 65 && code <= 90) || (code >= 97 && code <= 122) ||
         (code >= 48 && code <= 57) || ch === '-' || ch === ':' || ch === '_';
}

function isAttrNameChar(ch, first) {
  if (!ch) return false;
  const code = ch.charCodeAt(0);
  if (first) {
    return (code >= 65 && code <= 90) || (code >= 97 && code <= 122) || ch === '_' || ch === ':';
  }
  return (code >= 65 && code <= 90) || (code >= 97 && code <= 122) ||
         (code >= 48 && code <= 57) || ch === '-' || ch === '_' || ch === ':';
}

function mergeTextNodes(nodes) {
  const out = [];
  for (const node of nodes) {
    if (node.type === 'text' && out.length > 0 && out[out.length - 1].type === 'text') {
      const prev = out[out.length - 1];
      prev.content += node.content;
      prev.end = node.end;
    } else {
      out.push(node);
    }
  }
  return out;
}
