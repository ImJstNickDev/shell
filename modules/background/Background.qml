pragma ComponentBehavior: Bound

import QtQuick
import Quickshell
import Quickshell.Wayland
import Caelestia.Config
import qs.components
import qs.components.containers
import qs.services

Variants {
    model: Screens.screens.filter(s => GlobalConfig.forScreen(s.name).background.enabled)

    StyledWindow {
        id: win

        required property ShellScreen modelData

        screen: modelData
        name: "background"

        WlrLayershell.exclusionMode: ExclusionMode.Ignore

        WlrLayershell.layer:
            Wallpapers.showPreview
                || LiveWallpapers.active
                || !contentItem.Config.background.wallpaperEnabled
                ? WlrLayer.Bottom
                : WlrLayer.Background

        /*
         * Il nero viene disegnato insieme al wallpaper statico,
         * così partecipa al fade della preview senza coprire
         * immediatamente il live wallpaper.
         */
        color: "transparent"
        surfaceFormat.opaque: false

        anchors.top: true
        anchors.bottom: true
        anchors.left: true
        anchors.right: true

        ShellState.ComponentRef {
            screen: win.screen
            slot: "background"
            component: win
        }

        Item {
            id: behindClock

            anchors.fill: parent

            Item {
                id: staticWallpaperLayer

                anchors.fill: parent

                /*
                 * I cambiamenti di LiveWallpapers.active vengono
                 * applicati immediatamente.
                 *
                 * Soltanto Wallpapers.showPreview avvia il fade.
                 */
                function syncImmediately(): void {
                    previewFade.stop();

                    opacity =
                        !LiveWallpapers.active
                        || Wallpapers.showPreview
                            ? 1
                            : 0;
                }

                Component.onCompleted:
                    syncImmediately()

                Connections {
                    target: LiveWallpapers

                    function onActiveChanged(): void {
                        staticWallpaperLayer.syncImmediately();
                    }
                }

                Connections {
                    target: Wallpapers

                    function onShowPreviewChanged(): void {
                        /*
                         * Senza un live wallpaper lasciamo gestire
                         * le normali transizioni al componente
                         * Wallpaper upstream.
                         */
                        if (!LiveWallpapers.active) {
                            staticWallpaperLayer.syncImmediately();
                            return;
                        }

                        previewFade.stop();
                        previewFade.from =
                            staticWallpaperLayer.opacity;

                        previewFade.to =
                            Wallpapers.showPreview
                                ? 1
                                : 0;

                        previewFade.restart();
                    }
                }

                Anim {
                    id: previewFade

                    target: staticWallpaperLayer
                    property: "opacity"
                    type: Anim.DefaultEffects
                }

                Rectangle {
                    anchors.fill: parent
                    color: "black"
                }

                Loader {
                    id: wallpaper

                    asynchronous: true
                    anchors.fill: parent

                    active:
                        Config.background.wallpaperEnabled

                    sourceComponent: Wallpaper {}
                }
            }

            Visualiser {
                anchors.fill: parent
                screen: win.modelData
                wallpaper: wallpaper
            }
        }

        Loader {
            id: clockLoader

            asynchronous: true
            active: Config.background.desktopClock.enabled

            anchors.margins: Tokens.padding.extraLargeIncreased
            anchors.leftMargin: Tokens.padding.extraLargeIncreased + Tokens.sizes.bar.innerWidth + Math.max(Tokens.padding.small, Config.border.thickness)

            state: Config.background.desktopClock.position
            states: [
                State {
                    name: "top-left"

                    AnchorChanges {
                        target: clockLoader
                        anchors.top: parent.top
                        anchors.left: parent.left
                    }
                },
                State {
                    name: "top-center"

                    AnchorChanges {
                        target: clockLoader
                        anchors.top: parent.top
                        anchors.horizontalCenter: parent.horizontalCenter
                    }
                },
                State {
                    name: "top-right"

                    AnchorChanges {
                        target: clockLoader
                        anchors.top: parent.top
                        anchors.right: parent.right
                    }
                },
                State {
                    name: "middle-left"

                    AnchorChanges {
                        target: clockLoader
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                    }
                },
                State {
                    name: "middle-center"

                    AnchorChanges {
                        target: clockLoader
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.horizontalCenter: parent.horizontalCenter
                    }
                },
                State {
                    name: "middle-right"

                    AnchorChanges {
                        target: clockLoader
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.right: parent.right
                    }
                },
                State {
                    name: "bottom-left"

                    AnchorChanges {
                        target: clockLoader
                        anchors.bottom: parent.bottom
                        anchors.left: parent.left
                    }
                },
                State {
                    name: "bottom-center"

                    AnchorChanges {
                        target: clockLoader
                        anchors.bottom: parent.bottom
                        anchors.horizontalCenter: parent.horizontalCenter
                    }
                },
                State {
                    name: "bottom-right"

                    AnchorChanges {
                        target: clockLoader
                        anchors.bottom: parent.bottom
                        anchors.right: parent.right
                    }
                }
            ]

            transitions: Transition {
                AnchorAnim {}
            }

            sourceComponent: DesktopClock {
                wallpaper: behindClock
                absX: clockLoader.x
                absY: clockLoader.y
            }
        }
    }
}
