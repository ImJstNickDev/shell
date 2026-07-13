import QtQuick
import QtMultimedia

Item {
    id: root

    property url source
    property bool paused: false
    property bool autoStart: true
    property string fitMode: "cover"

    /*
     * Diventa true soltanto quando:
     *
     * 1. è arrivato un frame con dimensioni valide;
     * 2. VideoOutput è stato reso visibile;
     * 3. sono trascorsi due cicli dell'event loop,
     *    lasciando al scene graph il tempo di presentarlo.
     */
    readonly property bool frameReady:
        firstFramePresented

    readonly property bool playing:
        player.playing

    readonly property int playbackState:
        player.playbackState

    readonly property int mediaStatus:
        player.mediaStatus

    readonly property var error:
        player.error

    readonly property string errorString:
        player.errorString

    readonly property int position:
        player.position

    property bool firstFrameSeen: false
    property bool firstFramePresented: false
    property bool outputAttached: false
    property bool componentCompleted: false

    /*
     * Invalida eventuali callback Qt.callLater appartenenti
     * a un video precedente.
     */
    property int sourceGeneration: 0

    signal firstFrameReady

    function attachOutput() {
        if (outputAttached)
            return;

        player.videoOutput = output;
        outputAttached = true;
    }

    function resetFrameState() {
        sourceGeneration++;

        firstFrameSeen = false;
        firstFramePresented = false;

        output.visible = false;
        output.clearOutput();
    }

    function scheduleFirstFrameReady(
        generation
    ) {
        /*
         * Il primo callLater permette a VideoOutput di
         * aggiornarsi dopo visible = true.
         *
         * Il secondo lascia trascorrere un ulteriore ciclo
         * prima di dichiarare il renderer pronto.
         */
        Qt.callLater(() => {
            Qt.callLater(() => {
                if (generation !== root.sourceGeneration)
                    return;

                if (!root.firstFrameSeen)
                    return;

                if (!root.source.toString())
                    return;

                if (root.firstFramePresented)
                    return;

                root.firstFramePresented = true;
                root.firstFrameReady();
            });
        });
    }

    function updateSource() {
        if (!componentCompleted)
            return;

        attachOutput();
        resetFrameState();

        player.stop();
        player.source = "";

        if (!source.toString())
            return;

        player.source = source;
        updatePlayback();
    }

    function updatePlayback() {
        if (!componentCompleted || !outputAttached)
            return;

        if (!source.toString()) {
            player.stop();
            return;
        }

        if (paused || !autoStart) {
            if (player.playbackState
                    === MediaPlayer.PlayingState) {
                player.pause();
            }

            return;
        }

        if (player.playbackState
                !== MediaPlayer.PlayingState) {
            player.play();
        }
    }

    function stop() {
        player.stop();
    }

    function play() {
        paused = false;
        updatePlayback();
    }

    function pause() {
        paused = true;
        updatePlayback();
    }

    anchors.fill: parent
    clip: true

    onSourceChanged:
        Qt.callLater(root.updateSource)

    onPausedChanged:
        root.updatePlayback()

    onAutoStartChanged:
        root.updatePlayback()

    Component.onCompleted: {
        componentCompleted = true;

        /*
         * Colleghiamo VideoOutput dopo che il componente
         * è entrato nella scena, così Qt conosce già il
         * renderer grafico.
         */
        Qt.callLater(root.updateSource);
    }

    Component.onDestruction: {
        sourceGeneration++;

        player.stop();
        player.source = "";
        player.videoOutput = null;

        output.clearOutput();
    }

    VideoOutput {
        id: output

        anchors.fill: parent

        /*
         * Rimane invisibile durante:
         *
         * - creazione della superficie;
         * - clearOutput();
         * - apertura del file;
         * - inizializzazione del decoder;
         * - attesa del primo frame valido.
         */
        visible:
            root.firstFrameSeen

        fillMode:
            root.fitMode === "contain"
                ? VideoOutput.PreserveAspectFit
                : VideoOutput.PreserveAspectCrop

        endOfStreamPolicy:
            VideoOutput.KeepLastFrame
    }

    Connections {
        target: output.videoSink

        function onVideoFrameChanged() {
            if (root.firstFrameSeen)
                return;

            if (!root.componentCompleted)
                return;

            if (!root.source.toString())
                return;

            /*
             * clearOutput() può provocare un cambiamento
             * del frame, ma in quel momento videoSize non
             * rappresenta ancora un frame video valido.
             */
            const size =
                output.videoSink.videoSize;

            if (size.width <= 0
                    || size.height <= 0) {
                return;
            }

            const generation =
                root.sourceGeneration;

            root.firstFrameSeen = true;
            output.visible = true;

            root.scheduleFirstFrameReady(
                generation
            );
        }
    }

    MediaPlayer {
        id: player

        activeAudioTrack: -1
        activeSubtitleTrack: -1

        autoPlay: false
        loops: MediaPlayer.Infinite
        videoOutput: null

        onErrorOccurred:
            (error, errorString) => {
                if (error === MediaPlayer.NoError)
                    return;

                console.warn(
                    "VideoWallpaper: playback error:",
                    errorString,
                    "source:",
                    root.source
                );
            }

        onMediaStatusChanged: {
            if (mediaStatus
                    === MediaPlayer.InvalidMedia) {
                console.warn(
                    "VideoWallpaper: invalid media:",
                    root.source,
                    player.errorString
                );

                return;
            }

            if (mediaStatus
                    === MediaPlayer.LoadedMedia
                    || mediaStatus
                    === MediaPlayer.BufferedMedia) {
                root.updatePlayback();
            }
        }
    }
}
