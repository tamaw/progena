/**
 * Progenitor Chat UI - Syntax Highlighting Module
 *
 * Provides syntax highlighting for code blocks using a lightweight approach.
 */

export class SyntaxHighlighter {
    constructor() {
        this.languages = {
            javascript: this.highlightJS.bind(this),
            js: this.highlightJS.bind(this),
            typescript: this.highlightTS.bind(this),
            ts: this.highlightTS.bind(this),
            python: this.highlightPython.bind(this),
            py: this.highlightPython.bind(this),
            json: this.highlightJSON.bind(this),
            html: this.highlightHTML.bind(this),
            css: this.highlightCSS.bind(this),
            sql: this.highlightSQL.bind(this),
            bash: this.highlightBash.bind(this),
            shell: this.highlightBash.bind(this),
            sh: this.highlightBash.bind(this),
            yaml: this.highlightYAML.bind(this),
            yml: this.highlightYAML.bind(this),
            markdown: this.highlightMarkdown.bind(this),
            md: this.highlightMarkdown.bind(this),
        };

        // Common patterns
        this.patterns = {
            string: /(["'`])(?:(?!\1)[^\\]|\\.)*?\1/g,
            comment: /\/\/.*$|\/\*[\s\S]*?\*\/|#.*$/gm,
            number: /\b-?\d+\.?\d*(?:e[+-]?\d+)?\b/gi,
            boolean: /\b(true|false|null|undefined|None|True|False)\b/g,
            keyword: /\b(function|const|let|var|if|else|for|while|return|class|import|export|from|async|await|try|catch|throw|new|this|super|extends|implements|interface|type|enum|public|private|protected|static|readonly|abstract|def|self|lambda|yield|with|as|in|is|not|and|or|pass|break|continue|raise|except|finally|global|nonlocal)\b/g,
            function: /\b([a-zA-Z_]\w*)\s*(?=\()/g,
            property: /\.([a-zA-Z_]\w*)/g,
            operator: /[+\-*/%=<>!&|^~?:]+/g,
            bracket: /[[\]{}()]/g,
            decorator: /@\w+/g,
        };
    }

    /**
     * Highlight code with the appropriate language highlighter.
     * @param {string} code - Code to highlight
     * @param {string} language - Language identifier
     * @returns {string} Highlighted HTML
     */
    highlight(code, language) {
        const lang = language?.toLowerCase() || '';
        const highlighter = this.languages[lang];

        if (highlighter) {
            return highlighter(code);
        }

        // Default highlighting for unknown languages
        return this.highlightGeneric(code);
    }

    /**
     * Escape HTML entities.
     * @param {string} code - Code to escape
     * @returns {string} Escaped code
     */
    escape(code) {
        return code
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }

    /**
     * Wrap text in a span with a class.
     * @param {string} text - Text to wrap
     * @param {string} className - CSS class
     * @returns {string} Wrapped HTML
     */
    wrap(text, className) {
        return `<span class="hl-${className}">${text}</span>`;
    }

    /**
     * Generic highlighting.
     * @param {string} code - Code to highlight
     * @returns {string} Highlighted HTML
     */
    highlightGeneric(code) {
        let escaped = this.escape(code);

        // Strings first (to protect them from other replacements)
        escaped = escaped.replace(this.patterns.string, match =>
            this.wrap(match, 'string'));

        // Comments
        escaped = escaped.replace(this.patterns.comment, match =>
            this.wrap(match, 'comment'));

        // Numbers
        escaped = escaped.replace(this.patterns.number, match =>
            this.wrap(match, 'number'));

        return escaped;
    }

    /**
     * JavaScript highlighting.
     * @param {string} code - Code to highlight
     * @returns {string} Highlighted HTML
     */
    highlightJS(code) {
        let result = this.escape(code);
        const tokens = [];
        let tokenId = 0;

        // Extract and protect strings
        result = result.replace(/(["'`])(?:(?!\1)[^\\]|\\.)*?\1/g, match => {
            const id = `__TOKEN_${tokenId++}__`;
            tokens.push({ id, html: this.wrap(match, 'string') });
            return id;
        });

        // Extract and protect comments
        result = result.replace(/\/\/.*$|\/\*[\s\S]*?\*\//gm, match => {
            const id = `__TOKEN_${tokenId++}__`;
            tokens.push({ id, html: this.wrap(match, 'comment') });
            return id;
        });

        // Keywords
        result = result.replace(/\b(const|let|var|function|class|if|else|for|while|do|switch|case|default|break|continue|return|throw|try|catch|finally|new|delete|typeof|instanceof|in|of|void|this|super|extends|import|export|from|as|async|await|yield|static|get|set|null|undefined|true|false|NaN|Infinity)\b/g,
            match => this.wrap(match, 'keyword'));

        // Built-in objects
        result = result.replace(/\b(console|window|document|Array|Object|String|Number|Boolean|Function|Promise|Map|Set|Date|Math|JSON|RegExp|Error)\b/g,
            match => this.wrap(match, 'builtin'));

        // Functions
        result = result.replace(/\b([a-zA-Z_$]\w*)\s*(?=\()/g,
            (_, name) => this.wrap(name, 'function'));

        // Numbers
        result = result.replace(/\b(\d+\.?\d*(?:e[+-]?\d+)?)\b/gi,
            match => this.wrap(match, 'number'));

        // Restore tokens
        tokens.forEach(({ id, html }) => {
            result = result.replace(id, html);
        });

        return result;
    }

    /**
     * TypeScript highlighting (extends JS).
     * @param {string} code - Code to highlight
     * @returns {string} Highlighted HTML
     */
    highlightTS(code) {
        let result = this.highlightJS(code);

        // TypeScript-specific keywords
        result = result.replace(/\b(interface|type|enum|namespace|module|declare|abstract|implements|readonly|keyof|infer|never|unknown|any)\b/g,
            match => this.wrap(match, 'keyword'));

        // Type annotations
        result = result.replace(/:\s*([A-Z][a-zA-Z]*(?:<[^>]+>)?)/g,
            (_, type) => ': ' + this.wrap(type, 'type'));

        return result;
    }

    /**
     * Python highlighting.
     * @param {string} code - Code to highlight
     * @returns {string} Highlighted HTML
     */
    highlightPython(code) {
        let result = this.escape(code);
        const tokens = [];
        let tokenId = 0;

        // Triple-quoted strings
        result = result.replace(/(['"])\1\1[\s\S]*?\1\1\1/g, match => {
            const id = `__TOKEN_${tokenId++}__`;
            tokens.push({ id, html: this.wrap(match, 'string') });
            return id;
        });

        // Regular strings
        result = result.replace(/(["'])(?:(?!\1)[^\\]|\\.)*?\1/g, match => {
            const id = `__TOKEN_${tokenId++}__`;
            tokens.push({ id, html: this.wrap(match, 'string') });
            return id;
        });

        // Comments
        result = result.replace(/#.*$/gm, match => {
            const id = `__TOKEN_${tokenId++}__`;
            tokens.push({ id, html: this.wrap(match, 'comment') });
            return id;
        });

        // Keywords
        result = result.replace(/\b(def|class|if|elif|else|for|while|try|except|finally|with|as|import|from|return|yield|raise|pass|break|continue|and|or|not|in|is|lambda|global|nonlocal|assert|del|True|False|None|async|await)\b/g,
            match => this.wrap(match, 'keyword'));

        // Decorators
        result = result.replace(/@\w+/g, match => this.wrap(match, 'decorator'));

        // Built-ins
        result = result.replace(/\b(print|len|range|str|int|float|list|dict|set|tuple|bool|type|isinstance|hasattr|getattr|setattr|open|input|super|self|cls)\b/g,
            match => this.wrap(match, 'builtin'));

        // Functions
        result = result.replace(/\b([a-zA-Z_]\w*)\s*(?=\()/g,
            (_, name) => this.wrap(name, 'function'));

        // Numbers
        result = result.replace(/\b(\d+\.?\d*(?:e[+-]?\d+)?j?)\b/gi,
            match => this.wrap(match, 'number'));

        // Restore tokens
        tokens.forEach(({ id, html }) => {
            result = result.replace(id, html);
        });

        return result;
    }

    /**
     * JSON highlighting.
     * @param {string} code - Code to highlight
     * @returns {string} Highlighted HTML
     */
    highlightJSON(code) {
        let result = this.escape(code);

        // Property keys
        result = result.replace(/"([^"]+)"(?=\s*:)/g,
            (_, key) => this.wrap(`"${key}"`, 'property'));

        // String values
        result = result.replace(/:\s*"([^"]*)"/g,
            (match, value) => ': ' + this.wrap(`"${value}"`, 'string'));

        // Numbers
        result = result.replace(/:\s*(-?\d+\.?\d*)/g,
            (_, num) => ': ' + this.wrap(num, 'number'));

        // Booleans and null
        result = result.replace(/:\s*(true|false|null)\b/g,
            (_, val) => ': ' + this.wrap(val, 'keyword'));

        return result;
    }

    /**
     * HTML highlighting.
     * @param {string} code - Code to highlight
     * @returns {string} Highlighted HTML
     */
    highlightHTML(code) {
        let result = this.escape(code);

        // Comments
        result = result.replace(/&lt;!--[\s\S]*?--&gt;/g,
            match => this.wrap(match, 'comment'));

        // Tags
        result = result.replace(/&lt;(\/?)([\w-]+)/g,
            (_, slash, tag) => this.wrap(`&lt;${slash}${tag}`, 'tag'));

        result = result.replace(/([\w-]+)(&gt;)/g,
            (_, tag, close) => this.wrap(tag, 'tag') + close);

        // Attributes
        result = result.replace(/\s([\w-]+)=/g,
            (_, attr) => ' ' + this.wrap(attr, 'attribute') + '=');

        // Attribute values
        result = result.replace(/=("(?:[^"]*)"|'(?:[^']*)')/g,
            (_, value) => '=' + this.wrap(value, 'string'));

        return result;
    }

    /**
     * CSS highlighting.
     * @param {string} code - Code to highlight
     * @returns {string} Highlighted HTML
     */
    highlightCSS(code) {
        let result = this.escape(code);

        // Comments
        result = result.replace(/\/\*[\s\S]*?\*\//g,
            match => this.wrap(match, 'comment'));

        // Selectors
        result = result.replace(/([.#]?[\w-]+)(?=\s*\{)/g,
            match => this.wrap(match, 'selector'));

        // Properties
        result = result.replace(/([\w-]+)(?=\s*:)/g,
            match => this.wrap(match, 'property'));

        // Values
        result = result.replace(/:\s*([^;{}]+)/g,
            (_, value) => ': ' + this.wrap(value.trim(), 'value'));

        // Numbers
        result = result.replace(/(\d+\.?\d*)(px|em|rem|%|vh|vw|s|ms)?/g,
            (_, num, unit) => this.wrap(num, 'number') + (unit || ''));

        // Colors
        result = result.replace(/#[\da-fA-F]{3,8}\b/g,
            match => this.wrap(match, 'color'));

        return result;
    }

    /**
     * SQL highlighting.
     * @param {string} code - Code to highlight
     * @returns {string} Highlighted HTML
     */
    highlightSQL(code) {
        let result = this.escape(code);

        // Comments
        result = result.replace(/--.*$/gm, match => this.wrap(match, 'comment'));

        // Strings
        result = result.replace(/'[^']*'/g, match => this.wrap(match, 'string'));

        // Keywords
        result = result.replace(/\b(SELECT|FROM|WHERE|AND|OR|NOT|IN|LIKE|BETWEEN|JOIN|INNER|LEFT|RIGHT|OUTER|ON|AS|ORDER|BY|GROUP|HAVING|LIMIT|OFFSET|INSERT|INTO|VALUES|UPDATE|SET|DELETE|CREATE|TABLE|ALTER|DROP|INDEX|PRIMARY|KEY|FOREIGN|REFERENCES|UNIQUE|NULL|DEFAULT|AUTO_INCREMENT|CASCADE|UNION|ALL|DISTINCT|COUNT|SUM|AVG|MIN|MAX|CASE|WHEN|THEN|ELSE|END|EXISTS|IS|TRUE|FALSE)\b/gi,
            match => this.wrap(match.toUpperCase(), 'keyword'));

        // Numbers
        result = result.replace(/\b\d+\.?\d*\b/g, match => this.wrap(match, 'number'));

        return result;
    }

    /**
     * Bash/Shell highlighting.
     * @param {string} code - Code to highlight
     * @returns {string} Highlighted HTML
     */
    highlightBash(code) {
        let result = this.escape(code);

        // Comments
        result = result.replace(/#.*$/gm, match => this.wrap(match, 'comment'));

        // Strings
        result = result.replace(/(["'])(?:(?!\1)[^\\]|\\.)*?\1/g,
            match => this.wrap(match, 'string'));

        // Variables
        result = result.replace(/\$\w+|\$\{[^}]+\}/g,
            match => this.wrap(match, 'variable'));

        // Keywords
        result = result.replace(/\b(if|then|else|elif|fi|for|while|do|done|case|esac|function|return|exit|export|source|alias|unalias|echo|read|cd|pwd|ls|cp|mv|rm|mkdir|chmod|chown|grep|find|sed|awk|cat|head|tail|sort|uniq|wc|xargs|sudo|apt|yum|pip|npm|git)\b/g,
            match => this.wrap(match, 'keyword'));

        return result;
    }

    /**
     * YAML highlighting.
     * @param {string} code - Code to highlight
     * @returns {string} Highlighted HTML
     */
    highlightYAML(code) {
        let result = this.escape(code);

        // Comments
        result = result.replace(/#.*$/gm, match => this.wrap(match, 'comment'));

        // Keys
        result = result.replace(/^(\s*)([\w-]+)(:)/gm,
            (_, space, key, colon) => space + this.wrap(key, 'property') + colon);

        // Strings
        result = result.replace(/(["'])(?:(?!\1)[^\\]|\\.)*?\1/g,
            match => this.wrap(match, 'string'));

        // Booleans and null
        result = result.replace(/:\s*(true|false|null|~)\b/gi,
            (_, val) => ': ' + this.wrap(val, 'keyword'));

        // Numbers
        result = result.replace(/:\s*(-?\d+\.?\d*)\b/g,
            (_, num) => ': ' + this.wrap(num, 'number'));

        return result;
    }

    /**
     * Markdown highlighting.
     * @param {string} code - Code to highlight
     * @returns {string} Highlighted HTML
     */
    highlightMarkdown(code) {
        let result = this.escape(code);

        // Headers
        result = result.replace(/^(#{1,6})\s+(.*)$/gm,
            (_, hashes, text) => this.wrap(hashes, 'keyword') + ' ' + this.wrap(text, 'string'));

        // Bold
        result = result.replace(/\*\*([^*]+)\*\*/g,
            (_, text) => this.wrap('**' + text + '**', 'strong'));

        // Italic
        result = result.replace(/\*([^*]+)\*/g,
            (_, text) => this.wrap('*' + text + '*', 'emphasis'));

        // Links
        result = result.replace(/\[([^\]]+)\]\(([^)]+)\)/g,
            (_, text, url) => '[' + this.wrap(text, 'string') + '](' + this.wrap(url, 'link') + ')');

        // Code
        result = result.replace(/`([^`]+)`/g,
            (_, code) => this.wrap('`' + code + '`', 'code'));

        // Lists
        result = result.replace(/^(\s*)([*+-]|\d+\.)\s/gm,
            (_, space, bullet) => space + this.wrap(bullet, 'keyword') + ' ');

        return result;
    }
}

// Create global instance
export const highlighter = new SyntaxHighlighter();
export default SyntaxHighlighter;
