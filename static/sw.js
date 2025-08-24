// Service Worker for Web Push Notifications and Offline Caching
const CACHE_NAME = 'tangxiaozhu-web-v2';
const STATIC_CACHE_NAME = 'tangxiaozhu-static-v1';
const RUNTIME_CACHE_NAME = 'tangxiaozhu-runtime-v1';

// 需要缓存的静态资源
const STATIC_ASSETS = [
    '/',
    '/static/manifest.json',
    '/static/ico.png',
    '/static/sw.js'
];

// CDN资源列表（这些需要优先缓存）
const CDN_ASSETS = [
    'https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css',
    'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css',
    'https://cdn.jsdelivr.net/npm/chart.js@3.9.1/dist/chart.min.js',
    'https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.0.1/dist/chartjs-plugin-zoom.min.js',
    'https://cdn.socket.io/4.5.0/socket.io.min.js',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js'
];

// Install event
self.addEventListener('install', function(event) {
    event.waitUntil(
        Promise.all([
            // 缓存静态资源
            caches.open(STATIC_CACHE_NAME)
                .then(function(cache) {
                    return cache.addAll(STATIC_ASSETS);
                })
                .catch(function(error) {
                    console.warn('Failed to cache static assets:', error);
                    // 静态资源缓存失败时继续安装
                    return Promise.resolve();
                }),
            
            // 缓存CDN资源（这些是CSS和JS文件，是样式问题的关键）
            caches.open(CACHE_NAME)
                .then(function(cache) {
                    return cache.addAll(CDN_ASSETS);
                })
                .catch(function(error) {
                    console.warn('Failed to cache CDN assets:', error);
                    // CDN资源缓存失败时继续安装
                    return Promise.resolve();
                })
        ])
    );
    
    // 强制激活新的Service Worker
    self.skipWaiting();
});

// Activate event - 清理旧缓存
self.addEventListener('activate', function(event) {
    event.waitUntil(
        caches.keys().then(function(cacheNames) {
            return Promise.all(
                cacheNames.map(function(cacheName) {
                    // 删除旧版本的缓存
                    if (cacheName !== CACHE_NAME && 
                        cacheName !== STATIC_CACHE_NAME && 
                        cacheName !== RUNTIME_CACHE_NAME &&
                        cacheName.startsWith('tangxiaozhu-')) {
                        console.log('Deleting old cache:', cacheName);
                        return caches.delete(cacheName);
                    }
                    return Promise.resolve();
                })
            );
        })
    );
    
    // 立即控制所有页面
    return self.clients.claim();
});

// Fetch event - 优化网络请求策略
self.addEventListener('fetch', function(event) {
    const url = new URL(event.request.url);
    
    // 对于Chrome扩展和其他非HTTP请求，直接跳过
    if (!event.request.url.startsWith('http')) {
        return;
    }
    
    // 对于导航请求，使用network-first策略
    if (event.request.mode === 'navigate') {
        event.respondWith(
            fetch(event.request)
                .catch(function() {
                    // 网络失败时尝试从缓存获取页面
                    return caches.match('/');
                })
        );
        return;
    }
    
    // 对于CDN资源（CSS和JS），使用cache-first策略
    if (CDN_ASSETS.some(cdnUrl => event.request.url.startsWith(cdnUrl.split('/').slice(0, 5).join('/'))) ||
        event.request.url.includes('bootstrap') || 
        event.request.url.includes('font-awesome') ||
        event.request.url.includes('chart.js') ||
        event.request.url.includes('socket.io')) {
        
        event.respondWith(
            caches.match(event.request)
                .then(function(response) {
                    if (response) {
                        return response;
                    }
                    
                    // 没有缓存时从网络获取并缓存
                    return fetch(event.request)
                        .then(function(response) {
                            // 只缓存成功的响应
                            if (response.status === 200) {
                                const responseClone = response.clone();
                                caches.open(RUNTIME_CACHE_NAME).then(function(cache) {
                                    cache.put(event.request, responseClone);
                                });
                            }
                            return response;
                        })
                        .catch(function(error) {
                            console.warn('Failed to fetch CDN resource:', error);
                            throw error;
                        });
                })
        );
        return;
    }
    
    // 对于静态资源，使用cache-first策略
    if (event.request.url.includes('/static/')) {
        event.respondWith(
            caches.match(event.request)
                .then(function(response) {
                    if (response) {
                        return response;
                    }
                    return fetch(event.request);
                })
        );
        return;
    }
    
    // 对于其他请求，使用network-first策略
    event.respondWith(
        fetch(event.request)
            .then(function(response) {
                // 只缓存成功的GET请求
                if (response.status === 200 && event.request.method === 'GET') {
                    const responseClone = response.clone();
                    caches.open(RUNTIME_CACHE_NAME).then(function(cache) {
                        cache.put(event.request, responseClone);
                    });
                }
                return response;
            })
            .catch(function(error) {
                // 网络失败时尝试从缓存获取
                return caches.match(event.request);
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
    
    if (event.data && event.data.type === 'CHECK_UPDATE') {
        // 检查更新并通知主线程
        self.registration.update().then(function() {
            event.ports[0].postMessage({
                type: 'UPDATE_CHECKED',
                status: 'completed'
            });
        }).catch(function(error) {
            event.ports[0].postMessage({
                type: 'UPDATE_CHECKED',
                status: 'error',
                error: error.message
            });
        });
    }
    
    if (event.data && event.data.type === 'CLEAR_CACHE') {
        // 清理指定缓存
        const cacheName = event.data.cacheName;
        if (cacheName) {
            caches.delete(cacheName).then(function() {
                console.log('Cache cleared:', cacheName);
            });
        }
    }
});
