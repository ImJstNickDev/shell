pragma ComponentBehavior: Bound

//@ pragma ShellId caelestia-livewallpaper
//@ pragma AppId org.caelestia.livewallpaper

import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

ShellRoot {
    id: root

    readonly property int fadeDuration: 260

    property string videoPath:
        Quickshell.env(
            "CAELESTIA_LIVE_WALLPAPER_VIDEO"
        )

    property string fitMode: {
        const value =
            Quickshell.env(
                "CAELESTIA_LIVE_WALLPAPER_FIT"
            );

        return value === "contain"
            ? "contain"
            : "cover";
    }

    property bool paused:
        Quickshell.env(
            "CAELESTIA_LIVE_WALLPAPER_PAUSED"
        ) === "1"

    readonly property bool testMode:
        Quickshell.env(
            "CAELESTIA_LIVE_WALLPAPER_TEST"
        ) === "1"

    readonly property bool active:
        videoPath.length > 0

    readonly property bool idle:
        !active
        && !fadingOut

    property bool fadingOut: false
    property bool quitting: false

    /*
     * Ogni set/clear invalida la richiesta precedente.
     *
     * readyGeneration viene aggiornato soltanto quando
     * la generazione corrente ha realmente presentato
     * tutti i frame e completato il fade-in.
     */
    property int requestGeneration: 0
    property int readyGeneration: -1

    /*
     * Quando cambiamo sorgente da un video già pronto,
     * aspettiamo di osservare il reset dei frame del
     * vecchio video prima di accettare la nuova readiness.
     */
    property bool readinessArmed: false
    property bool waitingForFrameReset: false

    readonly property int windowCount:
        rendererWindows.instances.length

    readonly property int readyWindowCount: {
        let count = 0;
        const windows = rendererWindows.instances;

        for (let index = 0;
                index < windows.length;
                index++) {
            if (windows[index].frameReady)
                count++;
        }

        return count;
    }

    readonly property int fadedInWindowCount: {
        let count = 0;
        const windows = rendererWindows.instances;

        for (let index = 0;
                index < windows.length;
                index++) {
            if (windows[index].fadeComplete)
                count++;
        }

        return count;
    }

    /*
     * Tutti i monitor hanno ricevuto almeno un frame
     * video valido.
     */
    readonly property bool allFramesReady:
        active
        && windowCount > 0
        && readyWindowCount === windowCount

    /*
     * Il contenuto comincia a comparire soltanto dopo che
     * ogni monitor ha ricevuto il suo primo frame.
     */
    readonly property bool revealContent:
        allFramesReady
        && !fadingOut

    readonly property bool currentRequestVisuallyReady:
        revealContent
        && fadedInWindowCount === windowCount

    /*
     * Non basta più un generico stato visivo pronto:
     * deve essere pronta esattamente l'ultima richiesta.
     */
    readonly property bool ready:
        currentRequestVisuallyReady
        && readinessArmed
        && readyGeneration === requestGeneration

    function markReadyIfCurrent(
        generation
    ) {
        if (generation !== requestGeneration)
            return;

        if (!readinessArmed)
            return;

        if (!currentRequestVisuallyReady)
            return;

        readyGeneration = generation;
    }

    function beginRequest() {
        requestGeneration++;
        readyGeneration = -1;

        return requestGeneration;
    }

    function setFitMode(mode) {
        if (mode !== "cover"
                && mode !== "contain") {
            return false;
        }

        fitMode = mode;
        return true;
    }

    function setVideo(path, mode) {
        const cleanPath =
            String(path || "").trim();

        if (!cleanPath || quitting)
            return 0;

        if (mode && !setFitMode(mode))
            return 0;

        const previousPath = videoPath;
        const generation = beginRequest();

        clearTimer.stop();

        fadingOut = false;
        paused = false;

        /*
         * Con la stessa sorgente non esiste alcun frame
         * vecchio da distinguere: possiamo riutilizzare
         * immediatamente lo stato corrente.
         */
        if (cleanPath === previousPath) {
            waitingForFrameReset = false;
            readinessArmed = true;

            videoPath = cleanPath;

            Qt.callLater(() => {
                root.markReadyIfCurrent(
                    generation
                );
            });

            return generation;
        }

        /*
         * Se il vecchio video era completamente pronto,
         * impediamo che il controller interpreti i suoi
         * frame come appartenenti alla nuova sorgente.
         */
        waitingForFrameReset =
            windowCount > 0
            && readyWindowCount === windowCount;

        readinessArmed =
            !waitingForFrameReset;

        videoPath = cleanPath;

        /*
         * Se partiamo dallo stato idle, i frame sono già
         * azzerati e non verrà emesso un ulteriore reset.
         */
        if (readyWindowCount < windowCount) {
            waitingForFrameReset = false;
            readinessArmed = true;
        }

        return generation;
    }

    function clearVideo() {
        beginRequest();

        readinessArmed = false;
        waitingForFrameReset = false;

        if (!active) {
            videoPath = "";
            paused = false;
            fadingOut = false;
            return;
        }

        fadingOut = true;
        clearTimer.restart();
    }

    function quitWithFade() {
        if (quitting)
            return;

        quitting = true;

        beginRequest();

        readinessArmed = false;
        waitingForFrameReset = false;

        if (!active || windowCount === 0) {
            Qt.quit();
            return;
        }

        fadingOut = true;
        quitTimer.restart();
    }

    onReadyWindowCountChanged: {
        /*
         * Abbiamo osservato il reset dei frame appartenenti
         * alla sorgente precedente. Da ora la readiness può
         * essere attribuita alla richiesta corrente.
         */
        if (waitingForFrameReset
                && readyWindowCount < windowCount) {
            waitingForFrameReset = false;
            readinessArmed = true;
        }

        markReadyIfCurrent(
            requestGeneration
        );
    }

    onReadinessArmedChanged:
        markReadyIfCurrent(
            requestGeneration
        )

    onCurrentRequestVisuallyReadyChanged:
        markReadyIfCurrent(
            requestGeneration
        )

    Component.onCompleted: {
        if (active) {
            requestGeneration = 1;
            readyGeneration = -1;
            readinessArmed = true;
        }
    }

    Timer {
        id: clearTimer

        interval: root.fadeDuration + 40
        repeat: false

        onTriggered: {
            root.videoPath = "";
            root.paused = false;
            root.fadingOut = false;

            root.readinessArmed = false;
            root.waitingForFrameReset = false;
        }
    }

    Timer {
        id: quitTimer

        interval: root.fadeDuration + 40
        repeat: false

        onTriggered:
            Qt.quit()
    }

    Variants {
        id: rendererWindows

        model: Quickshell.screens

        // qmllint disable uncreatable-type
        PanelWindow {
            id: window

            required property ShellScreen modelData

            readonly property bool frameReady:
                wallpaper.frameReady

            readonly property bool fadeComplete:
                content.opacity >= 0.999

            screen: modelData
            visible: root.active

            color: "transparent"
            surfaceFormat.opaque: false

            WlrLayershell.namespace:
                "caelestia-live-wallpaper"

            WlrLayershell.layer:
                root.testMode
                    ? WlrLayer.Bottom
                    : WlrLayer.Background

            WlrLayershell.exclusionMode:
                ExclusionMode.Ignore

            WlrLayershell.keyboardFocus:
                WlrKeyboardFocus.None

            anchors.top: true
            anchors.bottom: true
            anchors.left: true
            anchors.right: true

            Item {
                id: content

                anchors.fill: parent

                opacity:
                    root.revealContent
                        ? 1
                        : 0

                Behavior on opacity {
                    NumberAnimation {
                        duration:
                            root.fadeDuration

                        easing.type:
                            Easing.InOutCubic
                    }
                }

                Rectangle {
                    anchors.fill: parent
                    color: "black"
                }

                VideoWallpaper {
                    id: wallpaper

                    anchors.fill: parent

                    source:
                        root.videoPath

                    paused:
                        root.paused

                    autoStart: true

                    fitMode:
                        root.fitMode
                }
            }
        }
        // qmllint enable uncreatable-type
    }

    IpcHandler {
        target: "liveWallpaperRenderer"

        function status(): string {
            return JSON.stringify({
                active:
                    root.active,

                idle:
                    root.idle,

                ready:
                    root.ready,

                requestGeneration:
                    root.requestGeneration,

                readyGeneration:
                    root.readyGeneration,

                readinessArmed:
                    root.readinessArmed,

                waitingForFrameReset:
                    root.waitingForFrameReset,

                allFramesReady:
                    root.allFramesReady,

                fadingOut:
                    root.fadingOut,

                windowCount:
                    root.windowCount,

                readyWindowCount:
                    root.readyWindowCount,

                fadedInWindowCount:
                    root.fadedInWindowCount,

                videoPath:
                    root.videoPath,

                fitMode:
                    root.fitMode,

                paused:
                    root.paused,

                testMode:
                    root.testMode
            });
        }

        /*
         * Restituisce la generazione assegnata alla richiesta.
         * Il valore 0 indica che la richiesta è stata rifiutata.
         */
        function set(
            videoPath: string,
            fitMode: string
        ): int {
            return root.setVideo(
                videoPath,
                fitMode
            );
        }

        function setFit(mode: string): bool {
            return root.setFitMode(mode);
        }

        function pause(): void {
            root.paused = true;
        }

        function resume(): void {
            root.paused = false;
        }

        function togglePause(): void {
            root.paused = !root.paused;
        }

        function clear(): void {
            root.clearVideo();
        }

        function quit(): void {
            root.quitWithFade();
        }
    }
}
