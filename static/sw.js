// Service Worker for Web Push Notifications
const CACHE_NAME = 'tangxiaozhu-web-v1';
const urlsToCache = [
    '/',
    '/static/manifest.json',
    '/static/icon.png',
    '/static/icon.png'
];

// Install event
self.addEventListener('install', function(event) {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(function(cache) {
                return cache.addAll(urlsToCache);
            })
    );
});

// Fetch event
self.addEventListener('fetch', function(event) {
    event.respondWith(
        caches.match(event.request)
            .then(function(response) {
                // Return cached version or fetch from network
                return response || fetch(event.request);
            }
        )
    );
});

// Push event
self.addEventListener('push', function(event) {
    console.log('Push event received:', event);
    
    let notificationData = {
        title: '糖小助',
        body: '您有新的血糖分析报告',
        icon: '/static/icon-192.png',
        badge: '/static/icon-192.png',
        tag: 'nightscout-notification',
        requireInteraction: true,
        actions: [
            {
                action: 'view',
                title: '查看详情',
                icon: '/static/icon-192.png'
            },
            {
                action: 'dismiss',
                title: '关闭',
                icon: '/static/icon-192.png'
            }
        ]
    };

    if (event.data) {
        try {
            const data = event.data.json();
            notificationData.title = data.title || notificationData.title;
            notificationData.body = data.message || data.body || notificationData.body;
            notificationData.data = data;
        } catch (e) {
            console.error('Error parsing push data:', e);
            notificationData.body = event.data.text() || notificationData.body;
        }
    }

    event.waitUntil(
        self.registration.showNotification(notificationData.title, notificationData)
    );
});

// Notification click event
self.addEventListener('notificationclick', function(event) {
    console.log('Notification click received:', event);
    
    event.notification.close();

    if (event.action === 'view') {
        // Open the app
        event.waitUntil(
            clients.openWindow('/')
        );
    } else if (event.action === 'dismiss') {
        // Just close the notification
        return;
    } else {
        // Default action - open the app
        event.waitUntil(
            clients.openWindow('/')
        );
    }
});

// Background sync event (for future use)
self.addEventListener('sync', function(event) {
    if (event.tag === 'background-sync') {
        event.waitUntil(
            // Perform background sync operations
            console.log('Background sync triggered')
        );
    }
});

// Message event (for communication with main thread)
self.addEventListener('message', function(event) {
    console.log('Service Worker received message:', event.data);
    
    if (event.data && event.data.type === 'SKIP_WAITING') {
        self.skipWaiting();
    }
});
