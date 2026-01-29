/**
 * Progenitor Chat UI - Notifications Module
 *
 * Handles in-app notifications, browser notifications, and agent alerts.
 */

class NotificationManager {
    constructor() {
        this.container = document.getElementById('notification-container');
        this.alertModal = document.getElementById('alert-modal');
        this.notificationBtn = document.getElementById('btn-notifications');
        this.notificationsPanel = document.getElementById('notifications-panel');
        this.notificationsBackdrop = document.getElementById('notifications-backdrop');
        this.notificationsList = document.getElementById('notifications-list');

        this.notifications = [];
        this.notificationHistory = [];
        this.alerts = [];
        this.browserPermission = Notification.permission;
        this.isPanelOpen = false;

        this.init();
    }

    /**
     * Initialize notification system.
     */
    init() {
        // Alert modal handlers
        const alertClose = document.getElementById('alert-close');
        const alertDismiss = document.getElementById('alert-dismiss');
        const alertRespond = document.getElementById('alert-respond');

        alertClose?.addEventListener('click', () => this.hideAlertModal());
        alertDismiss?.addEventListener('click', () => this.dismissCurrentAlert());
        alertRespond?.addEventListener('click', () => this.respondToAlert());

        // Modal backdrop click
        this.alertModal?.querySelector('.modal-backdrop')?.addEventListener('click', () => {
            this.hideAlertModal();
        });

        // Notification button - opens panel
        this.notificationBtn?.addEventListener('click', () => this.toggleNotificationsPanel());

        // Panel close button
        const panelClose = document.getElementById('notifications-panel-close');
        panelClose?.addEventListener('click', () => this.closeNotificationsPanel());

        // Backdrop click closes panel
        this.notificationsBackdrop?.addEventListener('click', () => this.closeNotificationsPanel());

        // Clear all button
        const clearBtn = document.getElementById('clear-notifications');
        clearBtn?.addEventListener('click', () => {
            this.clearNotificationHistory();
            this.updateNotificationsList();
        });

        // Request browser notification permission if needed
        if (Config.notifications.enabled && this.browserPermission === 'default') {
            this.requestPermission();
        }
    }

    /**
     * Toggle the notifications panel.
     */
    toggleNotificationsPanel() {
        if (this.isPanelOpen) {
            this.closeNotificationsPanel();
        } else {
            this.openNotificationsPanel();
        }
    }

    /**
     * Open the notifications panel.
     */
    openNotificationsPanel() {
        this.isPanelOpen = true;
        this.updateNotificationsList();

        this.notificationsPanel?.classList.remove('hidden');
        this.notificationsBackdrop?.classList.remove('hidden');

        // Trigger reflow for animation
        this.notificationsPanel?.offsetHeight;
        this.notificationsBackdrop?.offsetHeight;

        this.notificationsPanel?.classList.add('visible');
        this.notificationsBackdrop?.classList.add('visible');
    }

    /**
     * Close the notifications panel.
     */
    closeNotificationsPanel() {
        this.isPanelOpen = false;

        this.notificationsPanel?.classList.remove('visible');
        this.notificationsBackdrop?.classList.remove('visible');

        // Hide after animation
        setTimeout(() => {
            if (!this.isPanelOpen) {
                this.notificationsPanel?.classList.add('hidden');
                this.notificationsBackdrop?.classList.add('hidden');
            }
        }, 200);
    }

    /**
     * Update the notifications list in the panel.
     */
    updateNotificationsList() {
        if (!this.notificationsList) return;

        if (this.notificationHistory.length === 0) {
            this.notificationsList.innerHTML = '<div class="empty-state">No notifications</div>';
            return;
        }

        this.notificationsList.innerHTML = this.notificationHistory
            .slice()
            .reverse()
            .map(n => {
                const icons = { info: 'ℹ️', success: '✅', warning: '⚠️', error: '❌' };
                const timeAgo = this.formatTimeAgo(n.timestamp);
                return `
                    <div class="notification-item ${n.type}">
                        <span class="notification-icon">${icons[n.type] || 'ℹ️'}</span>
                        <div class="notification-content">
                            <div class="notification-title">${this.escapeHtml(n.title)}</div>
                            <div class="notification-message">${this.escapeHtml(n.message)}</div>
                            <div class="notification-time">${timeAgo}</div>
                        </div>
                    </div>
                `;
            })
            .join('');
    }

    /**
     * Format time ago string.
     */
    formatTimeAgo(timestamp) {
        const seconds = Math.floor((Date.now() - timestamp) / 1000);
        if (seconds < 60) return 'Just now';
        if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
        if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
        return `${Math.floor(seconds / 86400)}d ago`;
    }

    /**
     * Clear notification history.
     */
    clearNotificationHistory() {
        this.notificationHistory = [];
        this.alerts = [];
        this.updateAlertBadge();
    }

    /**
     * Request browser notification permission.
     */
    async requestPermission() {
        if (!('Notification' in window)) {
            console.warn('Browser does not support notifications');
            return false;
        }

        try {
            const permission = await Notification.requestPermission();
            this.browserPermission = permission;
            return permission === 'granted';
        } catch (error) {
            console.warn('Failed to request notification permission:', error);
            return false;
        }
    }

    /**
     * Show an in-app notification.
     * @param {string} type - Notification type (info, success, warning, error)
     * @param {string} title - Notification title
     * @param {string} message - Notification message
     * @param {Object} options - Additional options
     * @returns {HTMLElement} Notification element
     */
    show(type, title, message, options = {}) {
        const notification = document.createElement('div');
        notification.className = `notification ${type}`;

        const icons = {
            info: 'ℹ️',
            success: '✅',
            warning: '⚠️',
            error: '❌'
        };

        notification.innerHTML = `
            <span class="notification-icon">${icons[type] || icons.info}</span>
            <div class="notification-content">
                <div class="notification-title">${this.escapeHtml(title)}</div>
                <div class="notification-message">${this.escapeHtml(message)}</div>
                ${options.actions ? this.renderNotificationActions(options.actions) : ''}
            </div>
            <button class="notification-close">×</button>
        `;

        // Close button handler
        notification.querySelector('.notification-close').addEventListener('click', () => {
            this.hideNotification(notification);
        });

        // Action handlers
        if (options.actions) {
            notification.querySelectorAll('.notification-actions button').forEach((btn, i) => {
                btn.addEventListener('click', () => {
                    options.actions[i].callback?.();
                    this.hideNotification(notification);
                });
            });
        }

        // Add to container
        this.container.appendChild(notification);
        this.notifications.push(notification);

        // Add to history for the panel
        this.notificationHistory.push({
            type,
            title,
            message,
            timestamp: Date.now()
        });

        // Keep history limited
        if (this.notificationHistory.length > 50) {
            this.notificationHistory.shift();
        }

        // Limit visible notifications
        while (this.notifications.length > Config.notifications.maxVisible) {
            const oldest = this.notifications.shift();
            this.hideNotification(oldest);
        }

        // Auto-hide
        if (!options.persistent) {
            const duration = options.duration || Config.notifications.autoHideDuration;
            setTimeout(() => this.hideNotification(notification), duration);
        }

        // Browser notification
        if (Config.notifications.enabled && this.browserPermission === 'granted') {
            this.showBrowserNotification(title, message, type);
        }

        // Sound
        if (Config.notifications.soundEnabled) {
            this.playNotificationSound(type);
        }

        return notification;
    }

    /**
     * Render notification action buttons.
     * @param {Array} actions - Action definitions
     * @returns {string} HTML string
     */
    renderNotificationActions(actions) {
        const buttons = actions.map((action, i) => {
            const className = action.primary ? 'primary' : '';
            return `<button class="${className}">${this.escapeHtml(action.label)}</button>`;
        }).join('');

        return `<div class="notification-actions">${buttons}</div>`;
    }

    /**
     * Hide and remove a notification.
     * @param {HTMLElement} notification - Notification element
     */
    hideNotification(notification) {
        if (!notification || !notification.parentNode) return;

        notification.classList.add('closing');
        setTimeout(() => {
            notification.remove();
            const index = this.notifications.indexOf(notification);
            if (index > -1) {
                this.notifications.splice(index, 1);
            }
        }, 300);
    }

    /**
     * Show a browser notification.
     * @param {string} title - Notification title
     * @param {string} body - Notification body
     * @param {string} type - Notification type
     */
    showBrowserNotification(title, body, type = 'info') {
        if (this.browserPermission !== 'granted') return;

        try {
            const notification = new Notification(`Progenitor: ${title}`, {
                body: body,
                icon: '/favicon.ico',
                badge: '/favicon.ico',
                tag: `progenitor-${type}`,
                requireInteraction: type === 'error'
            });

            notification.onclick = () => {
                window.focus();
                notification.close();
            };

            // Auto-close after a delay
            setTimeout(() => notification.close(), 10000);
        } catch (error) {
            console.warn('Failed to show browser notification:', error);
        }
    }

    /**
     * Play a notification sound.
     * @param {string} type - Notification type
     */
    playNotificationSound(type) {
        // Create a simple beep sound
        try {
            const audioContext = new (window.AudioContext || window.webkitAudioContext)();
            const oscillator = audioContext.createOscillator();
            const gainNode = audioContext.createGain();

            oscillator.connect(gainNode);
            gainNode.connect(audioContext.destination);

            // Different frequencies for different types
            const frequencies = {
                info: 440,
                success: 523,
                warning: 392,
                error: 330
            };

            oscillator.frequency.value = frequencies[type] || 440;
            oscillator.type = 'sine';
            gainNode.gain.value = 0.1;

            oscillator.start();
            oscillator.stop(audioContext.currentTime + 0.15);
        } catch (error) {
            // Audio not supported
        }
    }

    /**
     * Show an agent alert that requires user attention.
     * @param {string} title - Alert title
     * @param {string} message - Alert message
     * @param {Object} options - Alert options
     */
    showAlert(title, message, options = {}) {
        const alert = {
            id: Date.now(),
            title,
            message,
            options,
            timestamp: new Date()
        };

        this.alerts.push(alert);
        this.updateAlertBadge();

        // Show immediately if it's urgent
        if (options.urgent) {
            this.displayAlertModal(alert);

            // Also show browser notification
            this.showBrowserNotification(title, message, 'warning');
        } else {
            // Show in-app notification with action
            this.show('warning', title, message, {
                persistent: true,
                actions: [
                    {
                        label: 'View',
                        primary: true,
                        callback: () => this.displayAlertModal(alert)
                    },
                    {
                        label: 'Dismiss',
                        callback: () => this.dismissAlert(alert.id)
                    }
                ]
            });
        }
    }

    /**
     * Display the alert modal.
     * @param {Object} alert - Alert data
     */
    displayAlertModal(alert) {
        this.currentAlert = alert;

        document.getElementById('alert-title').textContent = alert.title;
        document.getElementById('alert-message').textContent = alert.message;

        this.alertModal?.classList.remove('hidden');
        this.alertModal?.classList.add('visible');
    }

    /**
     * Hide the alert modal.
     */
    hideAlertModal() {
        this.alertModal?.classList.remove('visible');
        this.alertModal?.classList.add('hidden');
        this.currentAlert = null;
    }

    /**
     * Dismiss the current alert.
     */
    dismissCurrentAlert() {
        if (this.currentAlert) {
            this.dismissAlert(this.currentAlert.id);
        }
        this.hideAlertModal();
    }

    /**
     * Dismiss an alert by ID.
     * @param {number} id - Alert ID
     */
    dismissAlert(id) {
        const index = this.alerts.findIndex(a => a.id === id);
        if (index > -1) {
            this.alerts.splice(index, 1);
            this.updateAlertBadge();
        }
    }

    /**
     * Respond to the current alert.
     */
    respondToAlert() {
        if (this.currentAlert?.options?.onRespond) {
            this.currentAlert.options.onRespond();
        }
        this.dismissCurrentAlert();
    }

    /**
     * Update the alert badge count.
     */
    updateAlertBadge() {
        const existingBadge = this.notificationBtn?.querySelector('.alert-badge');

        if (this.alerts.length > 0) {
            if (existingBadge) {
                existingBadge.textContent = this.alerts.length;
            } else if (this.notificationBtn) {
                const badge = document.createElement('span');
                badge.className = 'alert-badge';
                badge.textContent = this.alerts.length;
                this.notificationBtn.appendChild(badge);
            }
        } else {
            existingBadge?.remove();
        }
    }

    /**
     * Toggle notification history panel (legacy method, redirects to panel).
     */
    toggleNotificationHistory() {
        this.toggleNotificationsPanel();
    }

    /**
     * Show an agent interrupt dialog.
     * @param {string} message - Interrupt message
     * @param {boolean} urgent - Whether urgent
     * @param {Function} onResponse - Response callback
     */
    showInterrupt(message, urgent = false, onResponse = null) {
        this.showAlert('Agent Interrupt', message, {
            urgent: urgent,
            onRespond: onResponse
        });
    }

    /**
     * Clear all notifications.
     */
    clearAll() {
        this.notifications.forEach(n => n.remove());
        this.notifications = [];
        this.alerts = [];
        this.updateAlertBadge();
    }

    /**
     * Escape HTML special characters.
     * @param {string} text - Raw text
     * @returns {string} Escaped text
     */
    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Export for use in main.js
window.NotificationManager = NotificationManager;
