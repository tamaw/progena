/**
 * Progenitor Chat UI - Export/Import Module
 * 
 * Provides functionality to export and import chat sessions.
 */

export class ExportImport {
    constructor(chatManager, sessionManager) {
        this.chatManager = chatManager;
        this.sessionManager = sessionManager;
        
        this.init();
    }

    init() {
        this.createUI();
        this.bindEvents();
    }

    /**
     * Create export/import UI elements.
     */
    createUI() {
        // Add to settings modal or create standalone buttons
        const settingsBody = document.querySelector('.settings-body');
        if (!settingsBody) return;

        const exportSection = document.createElement('div');
        exportSection.className = 'settings-section export-import-section';
        exportSection.innerHTML = `
            <h3>📦 Export & Import</h3>
            <div class="export-import-buttons">
                <button id="export-session-btn" class="btn-secondary">
                    📤 Export Current Session
                </button>
                <button id="export-all-btn" class="btn-secondary">
                    📤 Export All Sessions
                </button>
                <button id="import-btn" class="btn-secondary">
                    📥 Import Sessions
                </button>
                <input type="file" id="import-file" accept=".json,.txt" style="display: none;">
            </div>
            <div class="export-format-options">
                <label>Export Format:</label>
                <select id="export-format">
                    <option value="json">JSON (Full)</option>
                    <option value="markdown">Markdown</option>
                    <option value="text">Plain Text</option>
                </select>
            </div>
        `;

        settingsBody.appendChild(exportSection);
    }

    /**
     * Bind event handlers.
     */
    bindEvents() {
        // Export current session
        document.getElementById('export-session-btn')?.addEventListener('click', () => {
            this.exportCurrentSession();
        });

        // Export all sessions
        document.getElementById('export-all-btn')?.addEventListener('click', () => {
            this.exportAllSessions();
        });

        // Import
        document.getElementById('import-btn')?.addEventListener('click', () => {
            document.getElementById('import-file')?.click();
        });

        document.getElementById('import-file')?.addEventListener('change', (e) => {
            const file = e.target.files?.[0];
            if (file) {
                this.importSessions(file);
                e.target.value = ''; // Reset for next import
            }
        });

        // Keyboard shortcut: Ctrl+Shift+E for export
        document.addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.shiftKey && e.key === 'E') {
                e.preventDefault();
                this.exportCurrentSession();
            }
        });
    }

    /**
     * Get export format.
     */
    getFormat() {
        return document.getElementById('export-format')?.value || 'json';
    }

    /**
     * Export current session.
     */
    exportCurrentSession() {
        const messages = this.chatManager?.messages || [];
        const sessionId = this.sessionManager?.activeSessionId;
        const session = this.sessionManager?.getSession(sessionId);

        if (messages.length === 0) {
            window.notifications?.show('warning', 'No Messages', 'No messages to export.');
            return;
        }

        const format = this.getFormat();
        const data = this.formatExport({
            session: session || { id: 'default', title: 'Chat Session' },
            messages
        }, format);

        const filename = this.generateFilename(session?.title || 'chat', format);
        this.downloadFile(data, filename, format);

        window.notifications?.show('success', 'Exported', `Session exported as ${filename}`);
    }

    /**
     * Export all sessions.
     */
    exportAllSessions() {
        const sessions = this.sessionManager?.getSessions() || [];
        
        if (sessions.length === 0) {
            window.notifications?.show('warning', 'No Sessions', 'No sessions to export.');
            return;
        }

        const format = this.getFormat();
        const allData = sessions.map(session => ({
            session,
            messages: session.messages || []
        }));

        const data = this.formatExportAll(allData, format);
        const filename = this.generateFilename('all-sessions', format);
        this.downloadFile(data, filename, format);

        window.notifications?.show('success', 'Exported', `${sessions.length} sessions exported.`);
    }

    /**
     * Format export data.
     */
    formatExport(data, format) {
        switch (format) {
            case 'markdown':
                return this.toMarkdown(data);
            case 'text':
                return this.toPlainText(data);
            default:
                return JSON.stringify(data, null, 2);
        }
    }

    /**
     * Format all sessions export.
     */
    formatExportAll(allData, format) {
        switch (format) {
            case 'markdown':
                return allData.map(d => this.toMarkdown(d)).join('\n\n---\n\n');
            case 'text':
                return allData.map(d => this.toPlainText(d)).join('\n\n========================================\n\n');
            default:
                return JSON.stringify({ sessions: allData, exportedAt: new Date().toISOString() }, null, 2);
        }
    }

    /**
     * Convert to Markdown format.
     */
    toMarkdown(data) {
        const { session, messages } = data;
        let md = `# ${session.title || 'Chat Session'}\n\n`;
        md += `*Exported: ${new Date().toLocaleString()}*\n\n`;
        
        for (const msg of messages) {
            const role = msg.role === 'user' ? '**You**' : '**Assistant**';
            const time = msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString() : '';
            md += `### ${role}${time ? ` (${time})` : ''}\n\n`;
            md += `${msg.content}\n\n`;
        }

        return md;
    }

    /**
     * Convert to plain text format.
     */
    toPlainText(data) {
        const { session, messages } = data;
        let txt = `${session.title || 'Chat Session'}\n`;
        txt += `${'='.repeat(40)}\n`;
        txt += `Exported: ${new Date().toLocaleString()}\n\n`;
        
        for (const msg of messages) {
            const role = msg.role === 'user' ? 'You' : 'Assistant';
            const time = msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString() : '';
            txt += `[${role}]${time ? ` ${time}` : ''}\n`;
            txt += `${msg.content}\n\n`;
        }

        return txt;
    }

    /**
     * Generate filename.
     */
    generateFilename(base, format) {
        const date = new Date().toISOString().slice(0, 10);
        const ext = format === 'json' ? 'json' : format === 'markdown' ? 'md' : 'txt';
        const safeName = base.replace(/[^a-z0-9]/gi, '-').toLowerCase();
        return `progenitor-${safeName}-${date}.${ext}`;
    }

    /**
     * Download file.
     */
    downloadFile(content, filename, format) {
        const mimeType = format === 'json' ? 'application/json' 
                       : format === 'markdown' ? 'text/markdown' 
                       : 'text/plain';
        
        const blob = new Blob([content], { type: mimeType });
        const url = URL.createObjectURL(blob);
        
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    /**
     * Import sessions from file.
     */
    async importSessions(file) {
        try {
            const text = await file.text();
            let data;

            // Try to parse as JSON
            try {
                data = JSON.parse(text);
            } catch {
                // Not JSON, might be plain text or markdown
                window.notifications?.show('error', 'Import Failed', 'Only JSON format can be imported.');
                return;
            }

            // Handle different structures
            if (data.sessions) {
                // Multi-session export
                this.importMultipleSessions(data.sessions);
            } else if (data.session && data.messages) {
                // Single session export
                this.importSingleSession(data);
            } else if (Array.isArray(data)) {
                // Array of messages (legacy format)
                this.importMessages(data);
            } else {
                window.notifications?.show('error', 'Invalid Format', 'Unrecognized export format.');
            }
        } catch (e) {
            window.notifications?.show('error', 'Import Failed', `Error: ${e.message}`);
        }
    }

    /**
     * Import multiple sessions.
     */
    importMultipleSessions(sessions) {
        let imported = 0;

        for (const sessionData of sessions) {
            if (sessionData.session && sessionData.messages) {
                const newSession = this.sessionManager?.createSession(
                    `${sessionData.session.title} (imported)`
                );
                if (newSession) {
                    newSession.messages = sessionData.messages;
                    this.sessionManager?.saveSession(newSession);
                    imported++;
                }
            }
        }

        this.sessionManager?.renderSessionList?.();
        window.notifications?.show('success', 'Imported', `${imported} sessions imported.`);
    }

    /**
     * Import single session.
     */
    importSingleSession(data) {
        const newSession = this.sessionManager?.createSession(
            `${data.session.title} (imported)`
        );

        if (newSession) {
            newSession.messages = data.messages;
            this.sessionManager?.saveSession(newSession);
            this.sessionManager?.switchSession(newSession.id);
        }

        window.notifications?.show('success', 'Imported', 'Session imported successfully.');
    }

    /**
     * Import messages into current session.
     */
    importMessages(messages) {
        if (!this.chatManager) return;

        for (const msg of messages) {
            if (msg.role && msg.content) {
                this.chatManager.messages.push({
                    id: msg.id || Date.now().toString() + Math.random().toString(36).substr(2, 9),
                    role: msg.role,
                    content: msg.content,
                    timestamp: msg.timestamp || new Date().toISOString()
                });
            }
        }

        this.chatManager.renderAllMessages?.();
        this.chatManager.saveHistory();
        window.notifications?.show('success', 'Imported', `${messages.length} messages imported.`);
    }
}

export default ExportImport;
