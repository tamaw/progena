/**
 * Parse selectable Markdown headings and interactive question tags.
 *
 * Headings (##–######) and <questions>/<options>/<option> form the section
 * tree. Other markup stays in Markdown source for the renderer; code spans
 * and fenced code never create sections. Malformed constructs remain text
 * nodes rather than becoming interactive sections.
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
 * @property {Array<TextNode|SectionNode|HeadingNode>} children
 */

/**
 * @typedef {Object} HeadingNode
 * @property {'heading'} type
 * @property {number} level
 * @property {number} start
 * @property {number} end
 * @property {string} title
 * @property {Array<TextNode|SectionNode|HeadingNode>} children
 */

const ALLOWED_SECTION_TAGS = new Set(['questions', 'options', 'option']);
const VOID_TAGS = new Set([
  'area','base','br','col','embed','hr','img','input',
  'link','meta','param','source','track','wbr',
]);

/**
 * Parse a source string into an array of top-level nodes.
 * @param {string} source
 * @returns {Array<TextNode|SectionNode|HeadingNode>}
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
    return this.parseContent(() => false);
  }

  /**
   * Parse content until the stop predicate returns true or EOF.
   * @param {(() => boolean)|null} stopPredicate
   * @returns {Array<TextNode|SectionNode|HeadingNode>}
   */
  parseContent(stopPredicate) {
    const nodes = [];
    while (this.pos < this.len) {
      if (stopPredicate && stopPredicate()) break;
      const node = this.parseNode(stopPredicate);
      if (node) nodes.push(node);
    }
    return mergeTextNodes(nodes);
  }

  /**
   * Parse the next node at the current position.
   * @param {(() => boolean)|null} stopPredicate
   * @returns {TextNode|SectionNode|HeadingNode|null}
   */
  parseNode(stopPredicate) {
    if (stopPredicate && stopPredicate()) return null;

    // Code fence always wins
    if (this.peek(3) === '```' || this.peek(3) === '~~~') {
      return this.consumeCodeFence();
    }
    // Inline backtick
    if (this.peek(1) === '`') {
      return this.consumeInlineCode();
    }

    // Heading at start of line (##–###### only; H1 is plain text)
    if (this.isAtLineStart() && this.peek(1) === '#') {
      const heading = this.tryParseHeading(stopPredicate);
      if (heading) return heading;
      // Not a valid heading (e.g. #no-space or single #) → consume '#' as text
      this.pos++;
      return this.emitText(this.pos - 1, this.pos);
    }

    if (this.peek(1) === '<') {
      const tagStart = this.pos;
      const parsed = this.tryParseTag();
      if (parsed) {
        if (parsed.kind === 'open') {
          if (ALLOWED_SECTION_TAGS.has(parsed.name)) {
            if (VOID_TAGS.has(parsed.name)) {
              return this.emitSelfClosing(tagStart, parsed);
            }
            return this.parseAllowedSection(tagStart, parsed, stopPredicate);
          }
          // Disallowed tag → literal text
          return this.emitText(tagStart, this.pos);
        }
        if (parsed.kind === 'self') {
          if (ALLOWED_SECTION_TAGS.has(parsed.name)) {
            return this.emitSelfClosing(tagStart, parsed);
          }
          return this.emitText(tagStart, this.pos);
        }
        if (parsed.kind === 'close') {
          // Unexpected close tag → literal text
          return this.emitText(tagStart, this.pos);
        }
      }
      // Not a valid tag → consume one char as text
      this.pos++;
      return this.emitText(tagStart, this.pos);
    }

    return this.consumePlainText(stopPredicate);
  }

  /**
   * Attempt to parse a Markdown heading (##–######) at the current position.
   * On success, returns a HeadingNode and advances past the heading section.
   * On failure, leaves this.pos unchanged and returns null.
   */
  tryParseHeading(stopPredicate) {
    const start = this.pos;
    let level = 0;
    while (this.pos < this.len && this.source[this.pos] === '#' && level < 6) {
      level++;
      this.pos++;
    }
    if (level < 2) {
      this.pos = start;
      return null;
    }
    if (this.pos >= this.len || (this.source[this.pos] !== ' ' && this.source[this.pos] !== '\t')) {
      this.pos = start;
      return null;
    }
    // Skip whitespace after hashes
    while (this.pos < this.len && (this.source[this.pos] === ' ' || this.source[this.pos] === '\t')) {
      this.pos++;
    }
    const titleStart = this.pos;
    while (this.pos < this.len && this.source[this.pos] !== '\n' && this.source[this.pos] !== '\r') {
      this.pos++;
    }
    const title = this.source.slice(titleStart, this.pos).trim();
    // Consume newline after heading line
    if (this.pos < this.len && this.source[this.pos] === '\r') this.pos++;
    if (this.pos < this.len && this.source[this.pos] === '\n') this.pos++;

    // Parse children until next same-or-higher heading or stop predicate
    const children = this.parseHeadingChildren(level, stopPredicate);

    const end = this.pos;
    return { type: 'heading', level, start, end, title, children };
  }

  /**
   * Parse content inside a heading, stopping at heading boundaries.
   * This mirrors parseAllowedSection logic for nested sections so that
   * close tags for enclosing allowed sections are visible.
   */
  parseHeadingChildren(level, stopPredicate) {
    const nodes = [];
    while (this.pos < this.len) {
      if (stopPredicate && stopPredicate()) break;

      // Check for heading boundary (same or higher level)
      if (this.isAtLineStart() && this.peek(1) === '#') {
        const saved = this.pos;
        let nextLevel = 0;
        while (this.pos < this.len && this.source[this.pos] === '#' && nextLevel < 6) {
          nextLevel++;
          this.pos++;
        }
        const isHeading = nextLevel >= 1 && this.pos < this.len && (this.source[this.pos] === ' ' || this.source[this.pos] === '\t');
        this.pos = saved;
        if (isHeading && nextLevel <= level) break;
      }

      // Code fences and inline code are always literal text
      if (this.peek(3) === '```' || this.peek(3) === '~~~') {
        nodes.push(this.consumeCodeFence());
        continue;
      }
      if (this.peek(1) === '`') {
        nodes.push(this.consumeInlineCode());
        continue;
      }

      // Nested heading
      if (this.isAtLineStart() && this.peek(1) === '#') {
        const heading = this.tryParseHeading(stopPredicate);
        if (heading) {
          nodes.push(heading);
          continue;
        }
        this.pos++;
        nodes.push(this.emitText(this.pos - 1, this.pos));
        continue;
      }

      // Allowed sections
      if (this.peek(1) === '<') {
        const tagStart = this.pos;
        const parsed = this.tryParseTag();
        if (parsed) {
          if (parsed.kind === 'open') {
            if (ALLOWED_SECTION_TAGS.has(parsed.name)) {
              if (VOID_TAGS.has(parsed.name)) {
                nodes.push(this.emitSelfClosing(tagStart, parsed));
              } else {
                const section = this.parseAllowedSection(tagStart, parsed, () => {
                  if (stopPredicate && stopPredicate()) return true;
                  if (!this.isAtLineStart()) return false;
                  const saved = this.pos;
                  let nextLevel = 0;
                  while (this.pos < this.len && this.source[this.pos] === '#' && nextLevel < 6) {
                    nextLevel++;
                    this.pos++;
                  }
                  const isHeading = nextLevel >= 1 && this.pos < this.len && (this.source[this.pos] === ' ' || this.source[this.pos] === '\t');
                  this.pos = saved;
                  return isHeading && nextLevel <= level;
                });
                nodes.push(section);
              }
            } else {
              nodes.push(this.emitText(tagStart, this.pos));
            }
            continue;
          }
          if (parsed.kind === 'self') {
            if (ALLOWED_SECTION_TAGS.has(parsed.name)) {
              nodes.push(this.emitSelfClosing(tagStart, parsed));
            } else {
              nodes.push(this.emitText(tagStart, this.pos));
            }
            continue;
          }
          if (parsed.kind === 'close') {
            // Stop parsing heading children at any close tag;
            // let the caller (parseAllowedSection or top-level) decide
            // whether to match it or treat it as text.
            this.pos = tagStart;
            break;
          }
        }
        this.pos++;
        nodes.push(this.emitText(tagStart, this.pos));
        continue;
      }

      // Plain text
      const textStart = this.pos;
      while (this.pos < this.len) {
        if (stopPredicate && stopPredicate()) break;
        const ch = this.source[this.pos];
        if (ch === '<' || this.peek(3) === '```' || this.peek(3) === '~~~' || ch === '`') break;
        if (this.isAtLineStart() && ch === '#') break;
        this.pos++;
      }
      if (this.pos > textStart) {
        nodes.push(this.emitText(textStart, this.pos));
      }
    }
    return mergeTextNodes(nodes);
  }

  /**
   * Parse an allowed XML-like section starting with an already-consumed open tag.
   * Returns a SectionNode on success, or a TextNode on failure (unclosed).
   */
  parseAllowedSection(start, openTag, parentStopPredicate) {
    // Stack of { tag, start, openTag, children }
    const stack = [{ tag: openTag.name, start, openTag, children: [] }];

    while (this.pos < this.len) {
      if (parentStopPredicate && parentStopPredicate()) {
        // Parent boundary reached before section closed → unclosed
        return this.emitText(start, this.pos);
      }

      // Code fences and inline code are always literal text
      if (this.peek(3) === '```' || this.peek(3) === '~~~') {
        stack[stack.length - 1].children.push(this.consumeCodeFence());
        continue;
      }
      if (this.peek(1) === '`') {
        stack[stack.length - 1].children.push(this.consumeInlineCode());
        continue;
      }

      // Heading inside allowed section
      if (this.isAtLineStart() && this.peek(1) === '#') {
        const heading = this.tryParseHeading(parentStopPredicate);
        if (heading) {
          stack[stack.length - 1].children.push(heading);
          continue;
        }
        // Not a valid heading → consume '#' as text
        this.pos++;
        stack[stack.length - 1].children.push(this.emitText(this.pos - 1, this.pos));
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
          if (ALLOWED_SECTION_TAGS.has(parsed.name)) {
            if (VOID_TAGS.has(parsed.name)) {
              stack[stack.length - 1].children.push(this.emitSelfClosing(innerTagStart, parsed));
            } else {
              stack.push({ tag: parsed.name, start: innerTagStart, openTag: parsed, children: [] });
            }
          } else {
            // Disallowed tag inside section → literal text
            stack[stack.length - 1].children.push(this.emitText(innerTagStart, this.pos));
          }
          continue;
        }

        if (parsed.kind === 'self') {
          if (ALLOWED_SECTION_TAGS.has(parsed.name)) {
            stack[stack.length - 1].children.push(this.emitSelfClosing(innerTagStart, parsed));
          } else {
            stack[stack.length - 1].children.push(this.emitText(innerTagStart, this.pos));
          }
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
        if (parentStopPredicate && parentStopPredicate()) break;
        const ch = this.source[this.pos];
        if (ch === '<' || this.peek(3) === '```' || this.peek(3) === '~~~' || ch === '`') break;
        if (this.isAtLineStart() && ch === '#') break;
        this.pos++;
      }
      if (this.pos > textStart) {
        stack[stack.length - 1].children.push(this.emitText(textStart, this.pos));
      }
    }

    // Ran out of source with unclosed tag(s) → emit everything as plain text.
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

  consumePlainText(stopPredicate) {
    const start = this.pos;
    while (this.pos < this.len) {
      if (stopPredicate && stopPredicate()) break;
      const ch = this.source[this.pos];
      if (ch === '<' || this.peek(3) === '```' || this.peek(3) === '~~~' || ch === '`') break;
      if (this.isAtLineStart() && ch === '#') break;
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
        // Closing fence must be on its own line.
        let lineStart = this.pos - 1;
        while (lineStart >= start && this.source[lineStart] !== '\n') lineStart--;
        let onlyWsBefore = true;
        for (let p = lineStart + 1; p < this.pos; p++) {
          if (this.source[p] !== ' ' && this.source[p] !== '\t') {
            onlyWsBefore = false;
            break;
          }
        }
        if (!onlyWsBefore) {
          this.pos++;
          continue;
        }

        let end = this.pos;
        while (end < this.len && this.source[end] === marker) end++;
        if (end - this.pos >= size) {
          let after = end;
          while (after < this.len && (this.source[after] === ' ' || this.source[after] === '\t')) after++;
          if (after >= this.len) {
            this.pos = after;
            break;
          }
          if (this.source[after] === '\n') {
            this.pos = after + 1;
            break;
          }
          if (this.source[after] === '\r' && this.source[after + 1] === '\n') {
            this.pos = after + 2;
            break;
          }
        }
      }
      this.pos++;
    }
    return this.emitText(start, this.pos);
  }

  consumeInlineCode() {
    const start = this.pos;
    let tickCount = 0;
    while (this.pos < this.len && this.source[this.pos] === '`') {
      tickCount++;
      this.pos++;
    }
    while (this.pos < this.len) {
      if (this.source[this.pos] === '`') {
        const runStart = this.pos;
        while (this.pos < this.len && this.source[this.pos] === '`') {
          this.pos++;
        }
        if (this.pos - runStart === tickCount) {
          break;
        }
      } else {
        this.pos++;
      }
    }
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
          while (this.pos < this.len && !/\s|>|\//.test(this.source[this.pos])) this.pos++;
          attrValue = this.source.slice(valueStart, this.pos);
        }
      }
      // else boolean attribute with empty value
      attrs[attrName] = attrValue;
    }
    return { ok: false };
  }

  isAtLineStart() {
    if (this.pos === 0) return true;
    const prev = this.source[this.pos - 1];
    return prev === '\n' || prev === '\r';
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
