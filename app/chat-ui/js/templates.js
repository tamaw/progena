/**
 * Progenitor Chat UI - Prompt Templates Module
 *
 * Provides quick-access prompt templates for common tasks.
 */

export class PromptTemplates {
    constructor(chatManager) {
        this.chatManager = chatManager;
        this.storageKey = 'progenitor_templates';
        this.templates = [];

        this.init();
    }

    init() {
        this.loadTemplates();
        this.createUI();
        this.bindEvents();
    }

    /**
     * Default templates.
     */
    getDefaultTemplates() {
        return [
            {
                id: 'status',
                name: 'Check Status',
                icon: '📊',
                prompt: 'What is your current status? Show me active tasks and health.',
                category: 'system'
            },
            {
                id: 'backlog',
                name: 'View Backlog',
                icon: '📋',
                prompt: 'What tasks are in the backlog? List them by priority.',
                category: 'system'
            },
            {
                id: 'capabilities',
                name: 'Capabilities',
                icon: '🎯',
                prompt: 'What are your capabilities? What can you help me with?',
                category: 'system'
            },
            {
                id: 'explain',
                name: 'Explain Code',
                icon: '💡',
                prompt: 'Explain the following code:\n\n```\n{code}\n```',
                category: 'coding',
                hasPlaceholder: true
            },
            {
                id: 'review',
                name: 'Code Review',
                icon: '🔍',
                prompt: 'Review this code for bugs, security issues, and improvements:\n\n```\n{code}\n```',
                category: 'coding',
                hasPlaceholder: true
            },
            {
                id: 'refactor',
                name: 'Refactor Code',
                icon: '🔧',
                prompt: 'Refactor this code to be cleaner and more efficient:\n\n```\n{code}\n```',
                category: 'coding',
                hasPlaceholder: true
            },
            {
                id: 'test',
                name: 'Write Tests',
                icon: '🧪',
                prompt: 'Write unit tests for the following code:\n\n```\n{code}\n```',
                category: 'coding',
                hasPlaceholder: true
            },
            {
                id: 'debug',
                name: 'Debug Issue',
                icon: '🐛',
                prompt: 'Help me debug this issue:\n\nError: {error}\n\nCode:\n```\n{code}\n```',
                category: 'coding',
                hasPlaceholder: true
            },
            {
                id: 'document',
                name: 'Add Documentation',
                icon: '📝',
                prompt: 'Add comprehensive documentation to this code:\n\n```\n{code}\n```',
                category: 'coding',
                hasPlaceholder: true
            },
            {
                id: 'summarize',
                name: 'Summarize',
                icon: '📄',
                prompt: 'Summarize the following text concisely:\n\n{text}',
                category: 'general',
                hasPlaceholder: true
            },
            {
                id: 'translate',
                name: 'Translate',
                icon: '🌐',
                prompt: 'Translate the following to {language}:\n\n{text}',
                category: 'general',
                hasPlaceholder: true
            },
            {
                id: 'brainstorm',
                name: 'Brainstorm',
                icon: '💭',
                prompt: 'Help me brainstorm ideas for: {topic}',
                category: 'general',
                hasPlaceholder: true
            }
        ];
    }

    /**
     * Load templates from storage.
     */
    loadTemplates() {
        try {
            const saved = localStorage.getItem(this.storageKey);
            if (saved) {
                const custom = JSON.parse(saved);
                this.templates = [...this.getDefaultTemplates(), ...custom];
            } else {
                this.templates = this.getDefaultTemplates();
            }
        } catch (e) {
            this.templates = this.getDefaultTemplates();
        }
    }

    /**
     * Save custom templates.
     * @param {Array} customTemplates - Custom templates to save
     */
    saveTemplates(customTemplates) {
        try {
            localStorage.setItem(this.storageKey, JSON.stringify(customTemplates));
            this.loadTemplates();
            this.renderTemplates();
        } catch (e) {
            console.error('Failed to save templates:', e);
        }
    }

    /**
     * Create the templates UI.
     */
    createUI() {
        // Create templates button in input area
        const inputWrapper = document.querySelector('.input-wrapper');
        if (!inputWrapper) return;

        const templatesBtn = document.createElement('button');
        templatesBtn.id = 'templates-btn';
        templatesBtn.className = 'btn-templates';
        templatesBtn.title = 'Prompt Templates';
        templatesBtn.innerHTML = `
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
                <line x1="3" y1="9" x2="21" y2="9"></line>
                <line x1="9" y1="21" x2="9" y2="9"></line>
            </svg>
        `;

        inputWrapper.insertBefore(templatesBtn, inputWrapper.firstChild);

        // Create templates dropdown
        const dropdown = document.createElement('div');
        dropdown.id = 'templates-dropdown';
        dropdown.className = 'templates-dropdown hidden';
        dropdown.innerHTML = `
            <div class="templates-header">
                <h4>Prompt Templates</h4>
                <button class="templates-close">&times;</button>
            </div>
            <div class="templates-search">
                <input type="text" placeholder="Search templates..." class="templates-search-input">
            </div>
            <div class="templates-categories"></div>
            <div class="templates-list"></div>
            <div class="templates-footer">
                <button class="btn-add-template">+ Add Custom Template</button>
            </div>
        `;

        inputWrapper.appendChild(dropdown);

        this.dropdown = dropdown;
        this.renderTemplates();
    }

    /**
     * Render templates in the dropdown.
     */
    renderTemplates(filter = '') {
        const listEl = this.dropdown?.querySelector('.templates-list');
        const categoriesEl = this.dropdown?.querySelector('.templates-categories');
        if (!listEl || !categoriesEl) return;

        // Get unique categories
        const categories = [...new Set(this.templates.map(t => t.category))];

        // Render category filters
        categoriesEl.innerHTML = `
            <button class="category-btn active" data-category="all">All</button>
            ${categories.map(cat => `
                <button class="category-btn" data-category="${cat}">${this.capitalizeFirst(cat)}</button>
            `).join('')}
        `;

        // Filter templates
        let filtered = this.templates;
        if (filter) {
            const lowerFilter = filter.toLowerCase();
            filtered = this.templates.filter(t =>
                t.name.toLowerCase().includes(lowerFilter) ||
                t.prompt.toLowerCase().includes(lowerFilter)
            );
        }

        // Render templates
        listEl.innerHTML = filtered.map(template => `
            <div class="template-item" data-template-id="${template.id}">
                <span class="template-icon">${template.icon}</span>
                <div class="template-info">
                    <span class="template-name">${template.name}</span>
                    <span class="template-preview">${this.truncate(template.prompt, 50)}</span>
                </div>
                ${template.hasPlaceholder ? '<span class="template-placeholder-badge">⌨️</span>' : ''}
            </div>
        `).join('');
    }

    /**
     * Bind event handlers.
     */
    bindEvents() {
        const btn = document.getElementById('templates-btn');
        const dropdown = this.dropdown;

        // Toggle dropdown
        btn?.addEventListener('click', (e) => {
            e.stopPropagation();
            dropdown?.classList.toggle('hidden');
        });

        // Close button
        dropdown?.querySelector('.templates-close')?.addEventListener('click', () => {
            dropdown.classList.add('hidden');
        });

        // Search
        dropdown?.querySelector('.templates-search-input')?.addEventListener('input', (e) => {
            this.renderTemplates(e.target.value);
        });

        // Category filter
        dropdown?.querySelector('.templates-categories')?.addEventListener('click', (e) => {
            const btn = e.target.closest('.category-btn');
            if (!btn) return;

            // Update active state
            dropdown.querySelectorAll('.category-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            const category = btn.dataset.category;
            if (category === 'all') {
                this.renderTemplates();
            } else {
                const listEl = dropdown.querySelector('.templates-list');
                listEl.innerHTML = this.templates
                    .filter(t => t.category === category)
                    .map(template => `
                        <div class="template-item" data-template-id="${template.id}">
                            <span class="template-icon">${template.icon}</span>
                            <div class="template-info">
                                <span class="template-name">${template.name}</span>
                                <span class="template-preview">${this.truncate(template.prompt, 50)}</span>
                            </div>
                            ${template.hasPlaceholder ? '<span class="template-placeholder-badge">⌨️</span>' : ''}
                        </div>
                    `).join('');
            }
        });

        // Template selection
        dropdown?.querySelector('.templates-list')?.addEventListener('click', (e) => {
            const item = e.target.closest('.template-item');
            if (!item) return;

            const templateId = item.dataset.templateId;
            this.useTemplate(templateId);
            dropdown.classList.add('hidden');
        });

        // Add custom template
        dropdown?.querySelector('.btn-add-template')?.addEventListener('click', () => {
            this.showAddTemplateModal();
        });

        // Close on outside click
        document.addEventListener('click', (e) => {
            if (!dropdown?.contains(e.target) && !btn?.contains(e.target)) {
                dropdown?.classList.add('hidden');
            }
        });
    }

    /**
     * Use a template.
     * @param {string} templateId - Template ID
     */
    useTemplate(templateId) {
        const template = this.templates.find(t => t.id === templateId);
        if (!template) return;

        const input = this.chatManager?.inputElement;
        if (!input) return;

        let prompt = template.prompt;

        // Handle placeholders
        if (template.hasPlaceholder) {
            // Find placeholders like {code}, {text}, etc.
            const placeholders = prompt.match(/\{(\w+)\}/g);
            if (placeholders) {
                // For now, just insert the template and let user fill in
                input.value = prompt;
                input.focus();

                // Select first placeholder
                const firstPlaceholder = placeholders[0];
                const start = prompt.indexOf(firstPlaceholder);
                const end = start + firstPlaceholder.length;
                input.setSelectionRange(start, end);

                window.notifications?.show('info', 'Template Loaded',
                    'Replace the highlighted placeholder with your content.');
                return;
            }
        }

        // No placeholders - just insert
        input.value = prompt;
        input.focus();
        input.dispatchEvent(new Event('input'));
    }

    /**
     * Show modal to add custom template.
     */
    showAddTemplateModal() {
        const modal = document.createElement('div');
        modal.className = 'modal visible';
        modal.innerHTML = `
            <div class="modal-content">
                <div class="modal-header">
                    <h2>Add Custom Template</h2>
                    <button class="modal-close">&times;</button>
                </div>
                <div class="modal-body">
                    <div class="form-group">
                        <label>Name</label>
                        <input type="text" id="template-name" placeholder="My Template">
                    </div>
                    <div class="form-group">
                        <label>Icon (emoji)</label>
                        <input type="text" id="template-icon" placeholder="📝" maxlength="2">
                    </div>
                    <div class="form-group">
                        <label>Category</label>
                        <select id="template-category">
                            <option value="custom">Custom</option>
                            <option value="coding">Coding</option>
                            <option value="general">General</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Prompt</label>
                        <textarea id="template-prompt" rows="4" placeholder="Enter your prompt template..."></textarea>
                        <small>Use {placeholder} for user-fillable fields</small>
                    </div>
                </div>
                <div class="modal-footer">
                    <button class="btn-secondary modal-cancel">Cancel</button>
                    <button class="btn-primary modal-save">Save Template</button>
                </div>
            </div>
        `;

        document.body.appendChild(modal);

        // Close handlers
        const close = () => {
            modal.remove();
        };

        modal.querySelector('.modal-close').addEventListener('click', close);
        modal.querySelector('.modal-cancel').addEventListener('click', close);
        modal.addEventListener('click', (e) => {
            if (e.target === modal) close();
        });

        // Save handler
        modal.querySelector('.modal-save').addEventListener('click', () => {
            const name = document.getElementById('template-name').value.trim();
            const icon = document.getElementById('template-icon').value.trim() || '📝';
            const category = document.getElementById('template-category').value;
            const prompt = document.getElementById('template-prompt').value.trim();

            if (!name || !prompt) {
                window.notifications?.show('warning', 'Missing Fields', 'Please fill in name and prompt.');
                return;
            }

            const newTemplate = {
                id: `custom_${Date.now()}`,
                name,
                icon,
                prompt,
                category,
                hasPlaceholder: prompt.includes('{'),
                isCustom: true
            };

            // Get existing custom templates
            const saved = localStorage.getItem(this.storageKey);
            const custom = saved ? JSON.parse(saved) : [];
            custom.push(newTemplate);

            this.saveTemplates(custom);
            close();

            window.notifications?.show('success', 'Template Saved', 'Custom template added successfully.');
        });
    }

    /**
     * Truncate text.
     */
    truncate(text, maxLength) {
        if (text.length <= maxLength) return text;
        return text.substring(0, maxLength).trim() + '...';
    }

    /**
     * Capitalize first letter.
     */
    capitalizeFirst(str) {
        return str.charAt(0).toUpperCase() + str.slice(1);
    }
}

export default PromptTemplates;
