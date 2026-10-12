/**
 * PWA install guide — клик по логотипу открывает модальное окно установки.
 *
 * Android: стандартный beforeinstallprompt (только по действию пользователя;
 * автоматический мини-инфобар Chrome не подавляем — событие не перехватываем
 * через preventDefault).
 * iOS/iPadOS: beforeinstallprompt НЕ поддерживается — только инструкция
 * «Поделиться» → «На экран Домой» в Safari.
 */
window.GLB = window.GLB || {};

/** Сохранённое событие beforeinstallprompt (Android/Chrome, десктоп). */
GLB._deferredInstallPrompt = null;

GLB.isStandaloneDisplay = function () {
    try {
        if (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) {
            return true;
        }
    } catch (e) {}
    try {
        if (typeof navigator !== 'undefined' && navigator.standalone === true) return true;
    } catch (e) {}
    return false;
};

/**
 * Чистая функция определения платформы установки (тестируемая).
 *
 * @param {string} userAgent
 * @param {{standalone?: boolean, hasDeferredPrompt?: boolean, ipadOsDesktop?: boolean}} options
 * @returns {{platform: string, standalone: boolean, canNativePrompt: boolean, hasDeferredPrompt: boolean}}
 */
GLB.detectInstallPlatform = function (userAgent, options) {
    options = options || {};
    var ua = userAgent || '';
    var standalone = !!options.standalone;
    var hasDeferredPrompt = !!options.hasDeferredPrompt;
    // iPadOS 13+ маскируется под macOS-UserAgent — различаем по touch-точкам
    var isIPadOsDesktopUa = options.ipadOsDesktop === true;
    var isIOS = /iPad|iPhone|iPod/.test(ua) || isIPadOsDesktopUa;
    var isAndroid = /Android/.test(ua);

    if (isIOS) {
        // В Safari есть «Safari/», в сторонних iOS-браузерах — CriOS, FxiOS,
        // EdgiOS, OPiOS и т.п. (все они на WebKit и НЕ дают beforeinstallprompt).
        var isSafari =
            /Safari/.test(ua) && !/CriOS|FxiOS|EdgiOS|OPiOS|OPT|EdgA|DuckDuckGo|GSA/.test(ua);
        return {
            platform: isSafari ? 'ios-safari' : 'ios-other',
            standalone: standalone,
            canNativePrompt: false,
            hasDeferredPrompt: false,
        };
    }
    if (isAndroid) {
        return {
            platform: 'android',
            standalone: standalone,
            canNativePrompt: hasDeferredPrompt,
            hasDeferredPrompt: hasDeferredPrompt,
        };
    }
    return {
        platform: 'desktop',
        standalone: standalone,
        canNativePrompt: hasDeferredPrompt,
        hasDeferredPrompt: hasDeferredPrompt,
    };
};

GLB._currentInstallInfo = function () {
    var ua = '';
    try {
        ua = navigator.userAgent || '';
    } catch (e) {}
    var ipadOsDesktop = false;
    try {
        ipadOsDesktop =
            typeof navigator !== 'undefined' &&
            navigator.platform === 'MacIntel' &&
            navigator.maxTouchPoints > 1;
    } catch (e) {}
    return GLB.detectInstallPlatform(ua, {
        standalone: GLB.isStandaloneDisplay(),
        hasDeferredPrompt: !!GLB._deferredInstallPrompt,
        ipadOsDesktop: ipadOsDesktop,
    });
};

GLB._appendParagraphs = function (container, paragraphs) {
    paragraphs.forEach(function (text) {
        var p = document.createElement('p');
        p.textContent = text;
        container.appendChild(p);
    });
};

GLB._appendSteps = function (container, steps) {
    var ol = document.createElement('ol');
    ol.className = 'install-steps';
    steps.forEach(function (text) {
        var li = document.createElement('li');
        li.textContent = text;
        ol.appendChild(li);
    });
    container.appendChild(ol);
};

GLB._buildInstallContent = function (info) {
    var frag = document.createDocumentFragment();

    if (info.standalone) {
        var p = document.createElement('p');
        p.textContent = 'GL_JB уже установлен и запущен как приложение.';
        frag.appendChild(p);
        return frag;
    }

    if (info.platform === 'ios-safari') {
        GLB._appendParagraphs(frag, [
            'Установите GL_JB на экран «Домой» через Safari:',
        ]);
        GLB._appendSteps(frag, [
            'Нажмите кнопку «Поделиться» в нижней панели Safari (квадрат со стрелкой).',
            'В списке действий выберите «На экран Домой». Если пункта нет — прокрутите список вниз или откройте меню дополнительных действий (…).',
            'Проверьте название «GL_JB» и нажмите «Добавить».',
        ]);
        return frag;
    }

    if (info.platform === 'ios-other') {
        GLB._appendParagraphs(frag, [
            'Установка на iPhone/iPad работает через Safari. Откройте этот сайт в Safari:',
            'На iPhone: поделитесь ссылкой (например, в «Сообщениях») и откройте её в Safari, либо скопируйте адрес и вставьте в адресную строку Safari.',
            'Затем в Safari выполните: «Поделиться» → «На экран Домой» → «Добавить».',
        ]);
        return frag;
    }

    if (info.platform === 'android') {
        if (info.canNativePrompt) {
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'btn';
            btn.id = 'install-native-btn';
            btn.textContent = 'Установить';
            btn.addEventListener('click', function () {
                var promptEvent = GLB._deferredInstallPrompt;
                if (!promptEvent || typeof promptEvent.prompt !== 'function') return;
                try {
                    promptEvent.prompt();
                } catch (e) {
                    return;
                }
                var choice = promptEvent.userChoice;
                GLB._deferredInstallPrompt = null;
                if (choice && typeof choice.then === 'function') {
                    choice
                        .then(function () {
                            GLB.closeInstallGuide();
                        })
                        .catch(function () {});
                }
            });
            frag.appendChild(btn);
            GLB._appendParagraphs(frag, [
                'Или установите вручную:',
            ]);
        } else {
            GLB._appendParagraphs(frag, [
                'Системный диалог установки сейчас недоступен. Установите вручную:',
            ]);
        }
        GLB._appendSteps(frag, [
            'Откройте меню браузера (иконка ⋮ в правом верхнем углу).',
            'Выберите «Установить приложение» или «Добавить на главный экран».',
            'Подтвердите установку.',
        ]);
        return frag;
    }

    // desktop
    if (info.canNativePrompt) {
        var deskBtn = document.createElement('button');
        deskBtn.type = 'button';
        deskBtn.className = 'btn';
        deskBtn.id = 'install-native-btn';
        deskBtn.textContent = 'Установить';
        deskBtn.addEventListener('click', function () {
            var promptEvent = GLB._deferredInstallPrompt;
            if (!promptEvent || typeof promptEvent.prompt !== 'function') return;
            try {
                promptEvent.prompt();
            } catch (e) {
                return;
            }
            var choice = promptEvent.userChoice;
            GLB._deferredInstallPrompt = null;
            if (choice && typeof choice.then === 'function') {
                choice
                    .then(function () {
                        GLB.closeInstallGuide();
                    })
                    .catch(function () {});
            }
        });
        frag.appendChild(deskBtn);
        GLB._appendParagraphs(frag, ['Или через меню браузера:']);
    } else {
        GLB._appendParagraphs(frag, [
            'Установка PWA доступна в Chrome, Edge или Opera. Откройте сайт в одном из этих браузеров:',
        ]);
    }
    GLB._appendSteps(frag, [
        'Откройте меню браузера (⋮ или ⋯ в правом верхнем углу).',
        'Выберите «Установить GL_JB…» / «Установить приложение» / «Закрепить».',
        'Подтвердите установку.',
    ]);
    return frag;
};

GLB.openInstallGuide = function () {
    var modal = document.getElementById('install-modal');
    var body = document.getElementById('install-modal-body');
    if (!modal || !body) return;
    while (body.firstChild) body.removeChild(body.firstChild);
    body.appendChild(GLB._buildInstallContent(GLB._currentInstallInfo()));
    modal.hidden = false;
    try {
        document.body.style.overflow = 'hidden';
    } catch (e) {}
    var closeBtn = document.getElementById('install-modal-close');
    if (closeBtn && closeBtn.focus) closeBtn.focus();
};

GLB.closeInstallGuide = function () {
    var modal = document.getElementById('install-modal');
    if (!modal) return;
    modal.hidden = true;
    try {
        document.body.style.overflow = '';
    } catch (e) {}
};

GLB.initInstallGuide = function () {
    // Сохраняем beforeinstallprompt БЕЗ preventDefault: автоматический
    // мини-инфобар Chrome остаётся, дублирующих окон мы не показываем.
    window.addEventListener('beforeinstallprompt', function (event) {
        GLB._deferredInstallPrompt = event;
    });
    window.addEventListener('appinstalled', function () {
        GLB._deferredInstallPrompt = null;
        GLB.closeInstallGuide();
        if (typeof GLB.showToast === 'function') {
            GLB.showToast('GL_JB установлено');
        }
    });

    var brand = document.querySelector('.topbar .brand');
    if (brand) {
        brand.addEventListener('click', function (event) {
            event.preventDefault();
            GLB.openInstallGuide();
        });
    }

    var modal = document.getElementById('install-modal');
    var closeBtn = document.getElementById('install-modal-close');
    if (closeBtn) {
        closeBtn.addEventListener('click', GLB.closeInstallGuide);
    }
    if (modal) {
        modal.addEventListener('click', function (event) {
            if (event.target === modal) GLB.closeInstallGuide();
        });
    }
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && modal && !modal.hidden) {
            GLB.closeInstallGuide();
        }
    });
};

document.addEventListener('DOMContentLoaded', function () {
    GLB.initInstallGuide();
});
