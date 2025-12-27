(function() {
    var modules = [
        'modules/runtime.js',
        (currentLanguage === 'ru' ? 'modules/packages/ru.js' : 'modules/packages/en.js'),
        'modules/loader.js',
        'modules/fs.js',
        'modules/audio.js',
        'modules/graphics.js',
        'modules/events.js',
        'modules/fetch.js',
        (currentLanguage === 'ru' ? 'modules/asm_consts/ru.js' : 'modules/asm_consts/en.js'),
        // 'modules/cheats.js',
        'modules/main.js'
    ];

    if (cheatsEnabled)
        modules.push('modules/cheats.js');

    if (typeof importScripts === 'function') {
        importScripts.apply(null, modules);
    } else {
        var loadNext = function(i) {
            if (i < modules.length) {
                var s = document.createElement('script');
                s.src = modules[i];
                s.async = false; // Ensure order
                s.onload = function() { loadNext(i + 1); };
                s.onerror = function() { console.error('Failed to load module: ' + modules[i]); };
                document.body.appendChild(s);
            }
        };
        loadNext(0);
    }
})();
