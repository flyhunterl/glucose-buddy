// Service Worker for PWA with Enhanced Caching
const CACHE_NAME = 'tangxiaozhu-web-v2';
const STATIC_CACHE = 'tangxiaozhu-static-v2';
const DATA_CACHE = 'tangxiaozhu-data-v1';

// Critical resources to cache for fast PWA loading
const CRITICAL_URLS = [
    '/',
    '/static/manifest.json',
    '/static/ico.png',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css',
    'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css',
    'https://cdn.jsdelivr.net/npm/chart.js@3.9.1/dist/chart.min.js',
    'https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.0.1/dist/chartjs-plugin-zoom.min.js',
    'https://cdn.socket.io/4.5.0/socket.io.min.js'
];

// Additional resources to cache progressively
const EXTENDED_URLS = [
    'https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js'
];

// Install event - cache critical resources
self.addEventListener('install', function(event) {
    console.log('Service Worker: Installing...');
    event.waitUntil(
        caches.open(STATIC_CACHE)
            .then(function(cache) {
                console.log('Service Worker: Caching critical resources');
                return cache.addAll(CRITICAL_URLS);
            })
            .then(function() {
                console.log('Service Worker: Critical resources cached');
                return self.skipWaiting();
            })
            .catch(function(error) {
                console.error('Service Worker: Cache failed', error);
            })
    );
});

// Enhanced Fetch event with Network-First strategy
self.addEventListener('fetch', function(event) {
    // Skip non-GET requests
    if (event.request.method !== 'GET') {
        return;
    }

    const url = new URL(event.request.url);
    
    // For API requests, use network-first with fallback
    if (url.pathname.startsWith('/api/')) {
        event.respondWith(
            fetch(event.request)
                .then(function(response) {
                    // Clone response and cache it
                    if (response.status === 200) {
                        const responseClone = response.clone();
                        caches.open(DATA_CACHE).then(function(cache) {
                            cache.put(event.request, responseClone);
                        });
                    }
                    return response;
                })
                .catch(function(error) {
                    // Fallback to cached data
                    return caches.match(event.request).then(function(cached) {
                        if (cached) {
                            return cached;
                        }
                        // Return offline response
                        return new Response(JSON.stringify({
                            error: 'offline',
                            message: '网络连接不可用，请检查网络设置'
                        }), {
                            status: 503,
                            headers: { 'Content-Type': 'application/json' }
                        });
                    });
                })
        );
        return;
    }

    // For static resources, use cache-first strategy
    if (url.pathname.startsWith('/static/') || 
        url.hostname === 'cdn.jsdelivr.net' || 
        url.hostname === 'cdnjs.cloudflare.com') {
        
        event.respondWith(
            caches.match(event.request)
                .then(function(cached) {
                    if (cached) {
                        return cached;
                    }
                    
                    return fetch(event.request)
                        .then(function(response) {
                            // Cache successful responses
                            if (response.status === 200) {
                                const responseClone = response.clone();
                                caches.open(STATIC_CACHE).then(function(cache) {
                                    cache.put(event.request, responseClone);
                                });
                            }
                            return response;
                        });
                })
        );
        return;
    }

    // For HTML pages, use network-first with cache fallback
    event.respondWith(
        fetch(event.request)
            .then(function(response) {
                // Cache successful HTML responses
                if (response.status === 200 && response.headers.get('Content-Type')?.includes('text/html')) {
                    const responseClone = response.clone();
                    caches.open(STATIC_CACHE).then(function(cache) {
                        cache.put(event.request, responseClone);
                    });
                }
                return response;
            })
            .catch(function(error) {
                // Fallback to cached HTML
                return caches.match(event.request).then(function(cached) {
                    if (cached) {
                        return cached;
                    }
                    // Return offline page
                    return caches.match('/').then(function(homepage) {
                        return homepage || new Response('<html><body><h1>网络连接不可用</h1><p>请检查网络设置后重试</p></body></html>', {
                            status: 503,
                            headers: { 'Content-Type': 'text/html' }
                        });
                    });
                });
            })
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

// Activate event - clean up old caches
self.addEventListener('activate', function(event) {
    console.log('Service Worker: Activating...');
    event.waitUntil(
        caches.keys().then(function(cacheNames) {
            return Promise.all(
                cacheNames.map(function(cacheName) {
                    // Remove old cache versions
                    if (cacheName.startsWith('tangxiaozhu-') && 
                        cacheName !== STATIC_CACHE && 
                        cacheName !== DATA_CACHE &&
                        cacheName !== CACHE_NAME) {
                        console.log('Service Worker: Deleting old cache:', cacheName);
                        return caches.delete(cacheName);
                    }
                })
            );
        }).then(function() {
            console.log('Service Worker: Old caches cleaned up');
            return self.clients.claim();
        })
    );
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
    
    if (event.data && event.data.type === 'CACHE_URLS') {
        event.waitUntil(
            caches.open(STATIC_CACHE).then(function(cache) {
                return cache.addAll(event.data.urls);
            })
        );
    }
});

// Handle service worker errors
self.addEventListener('error', function(event) {
    console.error('Service Worker error:', event.error);
});
