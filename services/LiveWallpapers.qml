pragma Singleton

import QtQuick
import Quickshell
import Quickshell.Io

Singleton {
    id: root

    readonly property list<string> validVideoExtensions: [
        "mp4",
        "mkv",
        "webm"
    ]

    readonly property list<string> decoderModes: [
        "auto",
        "vaapi",
        "nvdec",
        "software"
    ]

    readonly property list<string> fitModes: [
        "cover",
        "contain"
    ]

    readonly property string controllerPath:
        Quickshell.shellPath(
            "utils/scripts/livewallpaperctl.py"
        )

    property alias enabled: props.enabled
    property alias currentVideo: props.currentVideo
    property alias currentPoster: props.currentPoster
    property alias decoderMode: props.decoderMode
    property alias fitMode: props.fitMode

    /*
     * Diventa true soltanto dopo che il renderer esterno
     * è realmente pronto.
     *
     * Durante il passaggio a uno statico diventa false
     * immediatamente, senza attendere la chiusura del
     * processo separato.
     */
    readonly property bool active:
        enabled
        && rendererRunning
        && !transitioningToStatic

    property bool rendererRunning: false
    property bool paused: false
    property bool busy: false

    property bool transitioningToStatic: false

    property string resolvedDecoder: ""
    property string vaapiDevice: ""
    property string lastError: ""

    property string pendingAction: ""
    property string pendingVideo: ""
    property string pendingPoster: ""
    property string pendingStdout: ""
    property string pendingStderr: ""
    property var pendingResult: ({})

    function fileExtension(path: string): string {
        const clean = String(path || "")
            .trim()
            .split(/[?#]/)[0]
            .toLowerCase();

        const index = clean.lastIndexOf(".");

        return index >= 0
            ? clean.slice(index + 1)
            : "";
    }

    function isVideo(path: string): bool {
        return validVideoExtensions.includes(
            fileExtension(path)
        );
    }

    function cleanPath(path: string): string {
        return String(path || "")
            .trim()
            .replace(/^file:\/\//, "")
            .split(/[?#]/)[0];
    }

    function parseJson(text: string): var {
        const clean = String(text || "").trim();

        if (!clean)
            return {};

        try {
            return JSON.parse(clean);
        } catch (error) {
            console.warn(
                "LiveWallpapers: invalid controller JSON:",
                error,
                clean
            );

            return {};
        }
    }

    /*
     * Adotta un renderer già avviato, incluso un renderer
     * lanciato direttamente tramite livewallpaperctl.py.
     *
     * Non esegue alcun comando esterno e può quindi essere
     * richiamata dal controller anche mentre busy è true.
     */
    function applyRunningState(
        videoPath: string,
        requestedDecoderValue: string,
        resolvedDecoderValue: string,
        fitModeValue: string,
        vaapiDeviceValue: string,
        pausedValue: bool
    ): bool {
        const video = cleanPath(videoPath);

        if (!video || !isVideo(video)) {
            console.warn(
                "LiveWallpapers: cannot adopt invalid video:",
                videoPath
            );

            return false;
        }

        const requestedDecoder = String(
            requestedDecoderValue || ""
        ).trim().toLowerCase();

        const resolved = String(
            resolvedDecoderValue || ""
        ).trim().toLowerCase();

        const requestedFit = String(
            fitModeValue || ""
        ).trim().toLowerCase();

        /*
         * Se il video è stato avviato esternamente non
         * conosciamo il suo poster.
         *
         * Durante un normale set proveniente da Caelestia,
         * pendingVideo coincide invece col video adottato:
         * in quel caso conserviamo pendingPoster finché il
         * comando non termina.
         */
        const sameSelection =
            currentVideo === video
            || pendingVideo === video;

        if (!sameSelection)
            currentPoster = "";

        currentVideo = video;

        if (decoderModes.includes(requestedDecoder))
            decoderMode = requestedDecoder;

        if (fitModes.includes(requestedFit))
            fitMode = requestedFit;

        enabled = true;
        rendererRunning = true;
        paused = pausedValue;
        transitioningToStatic = false;

        resolvedDecoder = resolved;
        vaapiDevice = String(
            vaapiDeviceValue || ""
        ).trim();

        lastError = "";

        return true;
    }

    /*
     * Sincronizza la rimozione del renderer anche quando
     * lo stop è stato eseguito direttamente dal controller.
     */
    function applyStoppedState(): void {
        enabled = false;
        rendererRunning = false;
        paused = false;
        transitioningToStatic = false;

        currentVideo = "";
        currentPoster = "";

        resolvedDecoder = "";
        vaapiDevice = "";

        lastError = "";
    }

    function beginCommand(
        arguments: list<string>,
        action: string
    ): bool {
        if (busy) {
            console.warn(
                "LiveWallpapers: controller is already busy"
            );

            return false;
        }

        pendingAction = action;
        pendingStdout = "";
        pendingStderr = "";
        pendingResult = {};

        lastError = "";
        busy = true;

        controllerProcess.exec([
            controllerPath,
            ...arguments
        ]);

        return true;
    }

    function setCurrent(
        videoPath: string,
        posterPath: string
    ): bool {
        const video = cleanPath(videoPath);
        const poster = cleanPath(posterPath);

        if (!video || !isVideo(video)) {
            console.warn(
                "LiveWallpapers: invalid video path:",
                videoPath
            );

            return false;
        }

        pendingVideo = video;
        pendingPoster = poster;
        transitioningToStatic = false;

        return beginCommand([
            "set",
            video,
            "--decoder",
            decoderMode,
            "--fit",
            fitMode
        ], "set");
    }

    function startCurrent(): bool {
        if (!currentVideo || !isVideo(currentVideo)) {
            console.warn(
                "LiveWallpapers: no valid persisted video"
            );

            rendererRunning = false;
            return false;
        }

        pendingVideo = currentVideo;
        pendingPoster = currentPoster;
        transitioningToStatic = false;

        return beginCommand([
            "set",
            currentVideo,
            "--decoder",
            decoderMode,
            "--fit",
            fitMode
        ], "start");
    }

    function clearCurrent(): bool {
        /*
         * Nascondiamo immediatamente il renderer dal punto
         * di vista della UI, mentre il processo separato
         * completa il fade-out e resta caldo in background.
         */
        transitioningToStatic = true;

        const started = beginCommand([
            "clear"
        ], "clear");

        if (!started)
            transitioningToStatic = false;

        return started;
    }

    function pause(): bool {
        if (!rendererRunning)
            return false;

        return beginCommand([
            "pause"
        ], "pause");
    }

    function resume(): bool {
        if (!rendererRunning)
            return false;

        return beginCommand([
            "resume"
        ], "resume");
    }

    function togglePause(): bool {
        if (!rendererRunning)
            return false;

        return beginCommand([
            "toggle"
        ], "toggle");
    }

    function refreshStatus(): bool {
        return beginCommand([
            "status"
        ], "status");
    }

    function requestDecoderMode(mode: string): bool {
        const clean = String(mode || "")
            .trim()
            .toLowerCase();

        if (!decoderModes.includes(clean)) {
            console.warn(
                "LiveWallpapers: invalid decoder mode:",
                mode
            );

            return false;
        }

        decoderMode = clean;

        if (enabled)
            startCurrent();

        return true;
    }

    function requestFitMode(mode: string): bool {
        const clean = String(mode || "")
            .trim()
            .toLowerCase();

        if (!fitModes.includes(clean)) {
            console.warn(
                "LiveWallpapers: invalid fit mode:",
                mode
            );

            return false;
        }

        fitMode = clean;

        if (enabled)
            startCurrent();

        return true;
    }

    function applyControllerResult(): void {
        const result = pendingResult;

        switch (pendingAction) {
        case "set":
            currentVideo = pendingVideo;
            currentPoster = pendingPoster;

            enabled = true;
            rendererRunning = result.running === true;
            paused = false;
            transitioningToStatic = false;

            resolvedDecoder =
                String(result.resolvedDecoder || "");

            vaapiDevice =
                String(result.vaapiDevice || "");

            break;

        case "start":
            rendererRunning = result.running === true;
            paused = false;
            transitioningToStatic = false;

            resolvedDecoder =
                String(result.resolvedDecoder || "");

            vaapiDevice =
                String(result.vaapiDevice || "");

            break;

        case "clear":
            applyStoppedState();
            break;

        case "pause":
            paused = true;
            break;

        case "resume":
            paused = false;
            break;

        case "toggle":
            paused = !paused;
            break;

        case "status": {
            const running =
                result.running === true;

            const state =
                result.state || {};

            const renderer =
                result.renderer || {};

            if (!running) {
                applyStoppedState();
                break;
            }

            const video =
                String(
                    renderer.videoPath
                    || state.video
                    || ""
                );

            const requestedDecoder =
                String(
                    state.requestedDecoder
                    || decoderMode
                    || "auto"
                );

            const resolved =
                String(
                    state.resolvedDecoder
                    || ""
                );

            const requestedFit =
                String(
                    renderer.fitMode
                    || state.fitMode
                    || fitMode
                    || "cover"
                );

            const device =
                String(
                    state.vaapiDevice
                    || ""
                );

            const rendererPaused =
                renderer.paused === true;

            applyRunningState(
                video,
                requestedDecoder,
                resolved,
                requestedFit,
                device,
                rendererPaused
            );

            break;
        }
        }
    }

    function handleExit(
        exitCode: int
    ): void {
        pendingResult = parseJson(
            pendingStdout
        );

        if (exitCode === 0) {
            applyControllerResult();
        } else {
            lastError =
                pendingStderr
                || pendingStdout
                || `Controller exited with code ${exitCode}`;

            console.warn(
                "LiveWallpapers:",
                pendingAction,
                "failed:",
                lastError
            );

            /*
             * Se il renderer non è mai partito, manteniamo
             * visibile il wallpaper statico.
             */
            if (pendingAction === "start"
                    || pendingAction === "set") {
                if (!rendererRunning)
                    enabled = false;
            }

            /*
             * Se lo stop fallisce, il renderer potrebbe
             * essere ancora attivo.
             */
            if (pendingAction === "clear")
                transitioningToStatic = false;
        }

        pendingAction = "";
        pendingVideo = "";
        pendingPoster = "";
        pendingStdout = "";
        pendingStderr = "";
        pendingResult = {};

        busy = false;
    }

    Component.onCompleted: {
        Qt.callLater(() => {
            /*
             * Ripristino persistente normale.
             */
            if (enabled
                    && currentVideo
                    && isVideo(currentVideo)) {
                root.startCurrent();
                return;
            }

            /*
             * Scopre anche un renderer avviato esternamente
             * prima del caricamento della shell principale.
             */
            root.refreshStatus();
        });
    }

    PersistentProperties {
        id: props

        property bool enabled: false

        property string currentVideo: ""
        property string currentPoster: ""

        property string decoderMode: "auto"
        property string fitMode: "cover"

        reloadableId: "liveWallpapers"
    }

    Process {
        id: controllerProcess

        stdout: StdioCollector {
            onStreamFinished:
                root.pendingStdout = text.trim()
        }

        stderr: StdioCollector {
            onStreamFinished:
                root.pendingStderr = text.trim()
        }

        onExited: exitCode => { // qmllint disable signal-handler-parameters
            root.handleExit(exitCode);
        }
    }

    IpcHandler {
        target: "liveWallpaper"

        function status(): string {
            return JSON.stringify({
                enabled:
                    root.enabled,

                active:
                    root.active,

                rendererRunning:
                    root.rendererRunning,

                transitioningToStatic:
                    root.transitioningToStatic,

                paused:
                    root.paused,

                busy:
                    root.busy,

                currentVideo:
                    root.currentVideo,

                currentPoster:
                    root.currentPoster,

                decoderMode:
                    root.decoderMode,

                resolvedDecoder:
                    root.resolvedDecoder,

                vaapiDevice:
                    root.vaapiDevice,

                fitMode:
                    root.fitMode,

                lastError:
                    root.lastError
            });
        }

        function set(
            videoPath: string,
            posterPath: string
        ): bool {
            return root.setCurrent(
                videoPath,
                posterPath
            );
        }

        function clear(): bool {
            return root.clearCurrent();
        }

        function pause(): bool {
            return root.pause();
        }

        function resume(): bool {
            return root.resume();
        }

        function togglePause(): bool {
            return root.togglePause();
        }

        function refresh(): bool {
            return root.refreshStatus();
        }

        function setDecoder(mode: string): bool {
            return root.requestDecoderMode(mode);
        }

        function setFit(mode: string): bool {
            return root.requestFitMode(mode);
        }

        /*
         * Chiamato da livewallpaperctl.py dopo che un
         * renderer, anche esterno, è diventato ready.
         */
        function syncStarted(
            videoPath: string,
            requestedDecoder: string,
            resolvedDecoder: string,
            fitMode: string,
            vaapiDevice: string
        ): bool {
            return root.applyRunningState(
                videoPath,
                requestedDecoder,
                resolvedDecoder,
                fitMode,
                vaapiDevice,
                false
            );
        }

        /*
         * Chiamato dal controller dopo uno stop esterno.
         */
        function syncStopped(): bool {
            root.applyStoppedState();
            return true;
        }

        /*
         * Mantiene coerente lo stato anche se pause/resume
         * vengono eseguiti direttamente dal controller.
         */
        function syncPaused(value: bool): bool {
            if (!root.rendererRunning)
                return false;

            root.paused = value;
            return true;
        }
    }
}
