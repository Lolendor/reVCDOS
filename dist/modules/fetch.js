var Fetch = {
    async openDatabase(dbname, dbversion) {
        return new Promise((resolve, reject) => {
            try {
                var openRequest = indexedDB.open(dbname, dbversion)
            } catch (e) {
                return reject(e)
            }
            openRequest.onupgradeneeded = event => {
                var db = event.target.result;
                if (db.objectStoreNames.contains("FILES")) {
                    db.deleteObjectStore("FILES")
                }
                db.createObjectStore("FILES")
            };
            openRequest.onsuccess = event => resolve(event.target.result);
            openRequest.onerror = reject
        })
    },
    async init() {
        Fetch.xhrs = new HandleAllocator;
        addRunDependency("library_fetch_init");
        try {
            var db = await Fetch.openDatabase("emscripten_filesystem", 1);
            Fetch.dbInstance = db
        } catch (e) {
            Fetch.dbInstance = false
        } finally {
            removeRunDependency("library_fetch_init")
        }
    }
};

function fetchXHR(fetch, onsuccess, onerror, onprogress, onreadystatechange) {
    var url = HEAPU32[fetch + 8 >> 2];
    if (!url) {
        onerror(fetch, "no url specified!");
        return
    }
    var url_ = replaceFetch(UTF8ToString(url));
    var fetch_attr = fetch + 108;
    var requestMethod = UTF8ToString(fetch_attr + 0);
    requestMethod ||= "GET";
    var timeoutMsecs = HEAPU32[fetch_attr + 56 >> 2];
    var userName = HEAPU32[fetch_attr + 68 >> 2];
    var password = HEAPU32[fetch_attr + 72 >> 2];
    var requestHeaders = HEAPU32[fetch_attr + 76 >> 2];
    var overriddenMimeType = HEAPU32[fetch_attr + 80 >> 2];
    var dataPtr = HEAPU32[fetch_attr + 84 >> 2];
    var dataLength = HEAPU32[fetch_attr + 88 >> 2];
    var fetchAttributes = HEAPU32[fetch_attr + 52 >> 2];
    var fetchAttrLoadToMemory = !!(fetchAttributes & 1);
    var fetchAttrStreamData = !!(fetchAttributes & 2);
    var fetchAttrSynchronous = !!(fetchAttributes & 64);
    var userNameStr = userName ? UTF8ToString(userName) : undefined;
    var passwordStr = password ? UTF8ToString(password) : undefined;
    var xhr = new XMLHttpRequest;
    xhr.withCredentials = !!HEAPU8[fetch_attr + 60];
    xhr.open(requestMethod, url_, !fetchAttrSynchronous, userNameStr, passwordStr);
    if (!fetchAttrSynchronous) xhr.timeout = timeoutMsecs;
    xhr.url_ = url_;
    xhr.responseType = "arraybuffer";
    if (overriddenMimeType) {
        var overriddenMimeTypeStr = UTF8ToString(overriddenMimeType);
        xhr.overrideMimeType(overriddenMimeTypeStr)
    }
    if (requestHeaders) {
        for (;;) {
            var key = HEAPU32[requestHeaders >> 2];
            if (!key) break;
            var value = HEAPU32[requestHeaders + 4 >> 2];
            if (!value) break;
            requestHeaders += 8;
            var keyStr = UTF8ToString(key);
            var valueStr = UTF8ToString(value);
            xhr.setRequestHeader(keyStr, valueStr)
        }
    }
    var id = Fetch.xhrs.allocate(xhr);
    HEAPU32[fetch >> 2] = id;
    var data = dataPtr && dataLength ? HEAPU8.slice(dataPtr, dataPtr + dataLength) : null;

    function saveResponseAndStatus() {
        var ptr = 0;
        var ptrLen = 0;
        if (xhr.response && fetchAttrLoadToMemory && HEAPU32[fetch + 12 >> 2] === 0) {
            ptrLen = xhr.response.byteLength
        }
        if (ptrLen > 0) {
            ptr = _realloc(HEAPU32[fetch + 12 >> 2], ptrLen);
            HEAPU8.set(new Uint8Array(xhr.response), ptr)
        }
        HEAPU32[fetch + 12 >> 2] = ptr;
        writeI53ToI64(fetch + 16, ptrLen);
        writeI53ToI64(fetch + 24, 0);
        var len = xhr.response ? xhr.response.byteLength : 0;
        if (len) {
            writeI53ToI64(fetch + 32, len)
        }
        HEAP16[fetch + 40 >> 1] = xhr.readyState;
        HEAP16[fetch + 42 >> 1] = xhr.status;
        if (xhr.statusText) stringToUTF8(xhr.statusText, fetch + 44, 64);
        if (fetchAttrSynchronous) {
            var ruPtr = stringToNewUTF8(xhr.responseURL);
            HEAPU32[fetch + 200 >> 2] = ruPtr
        }
    }
    xhr.onload = e => {
        if (!Fetch.xhrs.has(id)) {
            return
        }
        saveResponseAndStatus();
        if (xhr.status >= 200 && xhr.status < 300) {
            onsuccess(fetch, xhr, e)
        } else {
            onerror(fetch, e)
        }
    };
    xhr.onerror = e => {
        if (!Fetch.xhrs.has(id)) {
            return
        }
        saveResponseAndStatus();
        onerror(fetch, e)
    };
    xhr.ontimeout = e => {
        if (!Fetch.xhrs.has(id)) {
            return
        }
        onerror(fetch, e)
    };
    xhr.onprogress = e => {
        if (!Fetch.xhrs.has(id)) {
            return
        }
        var ptrLen = fetchAttrLoadToMemory && fetchAttrStreamData && xhr.response ? xhr.response.byteLength : 0;
        var ptr = 0;
        if (ptrLen > 0 && fetchAttrLoadToMemory && fetchAttrStreamData) {
            ptr = _realloc(HEAPU32[fetch + 12 >> 2], ptrLen);
            HEAPU8.set(new Uint8Array(xhr.response), ptr)
        }
        HEAPU32[fetch + 12 >> 2] = ptr;
        writeI53ToI64(fetch + 16, ptrLen);
        writeI53ToI64(fetch + 24, e.loaded - ptrLen);
        writeI53ToI64(fetch + 32, e.total);
        HEAP16[fetch + 40 >> 1] = xhr.readyState;
        var status = xhr.status;
        if (xhr.readyState >= 3 && xhr.status === 0 && e.loaded > 0) status = 200;
        HEAP16[fetch + 42 >> 1] = status;
        if (xhr.statusText) stringToUTF8(xhr.statusText, fetch + 44, 64);
        onprogress(fetch, e)
    };
    xhr.onreadystatechange = e => {
        if (!Fetch.xhrs.has(id)) {
            return
        }
        HEAP16[fetch + 40 >> 1] = xhr.readyState;
        if (xhr.readyState >= 2) {
            HEAP16[fetch + 42 >> 1] = xhr.status
        }
        if (!fetchAttrSynchronous && (xhr.readyState === 2 && xhr.responseURL.length > 0)) {
            var ruPtr = stringToNewUTF8(xhr.responseURL);
            HEAPU32[fetch + 200 >> 2] = ruPtr
        }
        onreadystatechange(fetch, e)
    };
    try {
        xhr.send(data)
    } catch (e) {
        onerror(fetch, e)
    }
}

function fetchCacheData(db, fetch, data, onsuccess, onerror) {
    if (!db) {
        onerror(fetch, 0, "IndexedDB not available!");
        return
    }
    var fetch_attr = fetch + 108;
    var destinationPath = HEAPU32[fetch_attr + 64 >> 2];
    destinationPath ||= HEAPU32[fetch + 8 >> 2];
    var destinationPathStr = UTF8ToString(destinationPath);
    try {
        var transaction = db.transaction(["FILES"], "readwrite");
        var packages = transaction.objectStore("FILES");
        var putRequest = packages.put(data, destinationPathStr);
        putRequest.onsuccess = event => {
            HEAP16[fetch + 40 >> 1] = 4;
            HEAP16[fetch + 42 >> 1] = 200;
            stringToUTF8("OK", fetch + 44, 64);
            onsuccess(fetch, 0, destinationPathStr)
        };
        putRequest.onerror = error => {
            HEAP16[fetch + 40 >> 1] = 4;
            HEAP16[fetch + 42 >> 1] = 413;
            stringToUTF8("Payload Too Large", fetch + 44, 64);
            onerror(fetch, 0, error)
        }
    } catch (e) {
        onerror(fetch, 0, e)
    }
}

function fetchLoadCachedData(db, fetch, onsuccess, onerror) {
    if (!db) {
        onerror(fetch, 0, "IndexedDB not available!");
        return
    }
    var fetch_attr = fetch + 108;
    var path = HEAPU32[fetch_attr + 64 >> 2];
    path ||= HEAPU32[fetch + 8 >> 2];
    var pathStr = UTF8ToString(path);
    try {
        var transaction = db.transaction(["FILES"], "readonly");
        var packages = transaction.objectStore("FILES");
        var getRequest = packages.get(pathStr);
        getRequest.onsuccess = event => {
            if (event.target.result) {
                var value = event.target.result;
                var len = value.byteLength || value.length;
                var ptr = _malloc(len);
                HEAPU8.set(new Uint8Array(value), ptr);
                HEAPU32[fetch + 12 >> 2] = ptr;
                writeI53ToI64(fetch + 16, len);
                writeI53ToI64(fetch + 24, 0);
                writeI53ToI64(fetch + 32, len);
                HEAP16[fetch + 40 >> 1] = 4;
                HEAP16[fetch + 42 >> 1] = 200;
                stringToUTF8("OK", fetch + 44, 64);
                onsuccess(fetch, 0, value)
            } else {
                HEAP16[fetch + 40 >> 1] = 4;
                HEAP16[fetch + 42 >> 1] = 404;
                stringToUTF8("Not Found", fetch + 44, 64);
                onerror(fetch, 0, "no data")
            }
        };
        getRequest.onerror = error => {
            HEAP16[fetch + 40 >> 1] = 4;
            HEAP16[fetch + 42 >> 1] = 404;
            stringToUTF8("Not Found", fetch + 44, 64);
            onerror(fetch, 0, error)
        }
    } catch (e) {
        onerror(fetch, 0, e)
    }
}

function fetchDeleteCachedData(db, fetch, onsuccess, onerror) {
    if (!db) {
        onerror(fetch, 0, "IndexedDB not available!");
        return
    }
    var fetch_attr = fetch + 108;
    var path = HEAPU32[fetch_attr + 64 >> 2];
    path ||= HEAPU32[fetch + 8 >> 2];
    var pathStr = UTF8ToString(path);
    try {
        var transaction = db.transaction(["FILES"], "readwrite");
        var packages = transaction.objectStore("FILES");
        var request = packages.delete(pathStr);
        request.onsuccess = event => {
            var value = event.target.result;
            HEAPU32[fetch + 12 >> 2] = 0;
            writeI53ToI64(fetch + 16, 0);
            writeI53ToI64(fetch + 24, 0);
            writeI53ToI64(fetch + 32, 0);
            HEAP16[fetch + 40 >> 1] = 4;
            HEAP16[fetch + 42 >> 1] = 200;
            stringToUTF8("OK", fetch + 44, 64);
            onsuccess(fetch, 0, value)
        };
        request.onerror = error => {
            HEAP16[fetch + 40 >> 1] = 4;
            HEAP16[fetch + 42 >> 1] = 404;
            stringToUTF8("Not Found", fetch + 44, 64);
            onerror(fetch, 0, error)
        }
    } catch (e) {
        onerror(fetch, 0, e)
    }
}

function _emscripten_start_fetch(fetch, successcb, errorcb, progresscb, readystatechangecb) {
    var fetch_attr = fetch + 108;
    var onsuccess = HEAPU32[fetch_attr + 36 >> 2];
    var onerror = HEAPU32[fetch_attr + 40 >> 2];
    var onprogress = HEAPU32[fetch_attr + 44 >> 2];
    var onreadystatechange = HEAPU32[fetch_attr + 48 >> 2];
    var fetchAttributes = HEAPU32[fetch_attr + 52 >> 2];
    var fetchAttrSynchronous = !!(fetchAttributes & 64);

    function doCallback(f) {
        if (fetchAttrSynchronous) {
            f()
        } else {
            callUserCallback(f)
        }
    }
    var reportSuccess = (fetch, xhr, e) => {
        doCallback(() => {
            if (onsuccess) getWasmTableEntry(onsuccess)(fetch);
            else successcb?.(fetch)
        })
    };
    var reportProgress = (fetch, e) => {
        doCallback(() => {
            if (onprogress) getWasmTableEntry(onprogress)(fetch);
            else progresscb?.(fetch)
        })
    };
    var reportError = (fetch, e) => {
        doCallback(() => {
            if (onerror) getWasmTableEntry(onerror)(fetch);
            else errorcb?.(fetch)
        })
    };
    var reportReadyStateChange = (fetch, e) => {
        doCallback(() => {
            if (onreadystatechange) getWasmTableEntry(onreadystatechange)(fetch);
            else readystatechangecb?.(fetch)
        })
    };
    var performUncachedXhr = (fetch, xhr, e) => {
        fetchXHR(fetch, reportSuccess, reportError, reportProgress, reportReadyStateChange)
    };
    var cacheResultAndReportSuccess = (fetch, xhr, e) => {
        var storeSuccess = (fetch, xhr, e) => {
            doCallback(() => {
                if (onsuccess) getWasmTableEntry(onsuccess)(fetch);
                else successcb?.(fetch)
            })
        };
        var storeError = (fetch, xhr, e) => {
            doCallback(() => {
                if (onsuccess) getWasmTableEntry(onsuccess)(fetch);
                else successcb?.(fetch)
            })
        };
        fetchCacheData(Fetch.dbInstance, fetch, xhr.response, storeSuccess, storeError)
    };
    var performCachedXhr = (fetch, xhr, e) => {
        fetchXHR(fetch, cacheResultAndReportSuccess, reportError, reportProgress, reportReadyStateChange)
    };
    var requestMethod = UTF8ToString(fetch_attr + 0);
    var fetchAttrReplace = !!(fetchAttributes & 16);
    var fetchAttrPersistFile = !!(fetchAttributes & 4);
    var fetchAttrNoDownload = !!(fetchAttributes & 32);
    if (requestMethod === "EM_IDB_STORE") {
        var ptr = HEAPU32[fetch_attr + 84 >> 2];
        var size = HEAPU32[fetch_attr + 88 >> 2];
        fetchCacheData(Fetch.dbInstance, fetch, HEAPU8.slice(ptr, ptr + size), reportSuccess, reportError)
    } else if (requestMethod === "EM_IDB_DELETE") {
        fetchDeleteCachedData(Fetch.dbInstance, fetch, reportSuccess, reportError)
    } else if (!fetchAttrReplace) {
        fetchLoadCachedData(Fetch.dbInstance, fetch, reportSuccess, fetchAttrNoDownload ? reportError : fetchAttrPersistFile ? performCachedXhr : performUncachedXhr)
    } else if (!fetchAttrNoDownload) {
        fetchXHR(fetch, fetchAttrPersistFile ? cacheResultAndReportSuccess : reportSuccess, reportError, reportProgress, reportReadyStateChange)
    } else {
        return 0
    }
    return fetch
}
