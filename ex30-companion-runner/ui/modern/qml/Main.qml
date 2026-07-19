import QtQuick
import EX30 1.0

// Scene root. The 1920x480 design canvas lives inside whatever window the
// host gives us: rotated -90° into the 480x1920 Pi window (matching the
// legacy RotatedView mapping), or scaled-to-fit unrotated for the Mac demo.
Item {
    id: root
    focus: true

    // boot → loading → dash
    property string phase: uiSkipBoot ? "dash" : "boot"

    Component.onCompleted: {
        if (uiFontFamily && uiFontFamily.length > 0)
            Theme.sans = uiFontFamily
    }

    // Boot and loading are always cinematic-dark; the dash follows the car.
    Binding {
        target: Theme
        property: "day"
        value: vehicle.isDay && root.phase === "dash"
    }

    Keys.onPressed: (event) => {
        if (event.key === Qt.Key_Space && root.phase !== "dash") {
            root.phase = (root.phase === "boot") ? "loading" : "dash"
            event.accepted = true
            return
        }
        if (typeof bench !== "undefined" && bench)
            bench.key(event.key, event.text)
    }

    Rectangle {
        anchors.fill: parent
        color: root.phase === "dash" ? Theme.bg : Theme.bootBg
        Behavior on color { ColorAnimation { duration: 500 } }
    }

    Item {
        id: canvas
        width: 1920
        height: 480
        anchors.centerIn: parent
        rotation: uiRotated ? -90 : 0
        scale: uiRotated
               ? Math.min(root.height / canvas.width, root.width / canvas.height)
               : Math.min(root.width / canvas.width, root.height / canvas.height)

        BootSplash {
            anchors.fill: parent
            active: root.phase === "boot"
            onFinished: if (root.phase === "boot") root.phase = "loading"
        }

        LoadingScreen {
            anchors.fill: parent
            active: root.phase === "loading"
            onReady: if (root.phase === "loading") root.phase = "dash"
        }

        // ── Dashboard ────────────────────────────────────────────────
        Item {
            id: dash
            anchors.fill: parent
            readonly property bool awake: root.phase === "dash"
            opacity: awake ? 1 : 0
            visible: opacity > 0.001
            Behavior on opacity {
                NumberAnimation { duration: 600; easing.type: Easing.OutCubic }
            }

            DriveScreen {
                anchors.fill: parent
                awake: dash.awake && !vehicle.isCharging
                opacity: vehicle.isCharging ? 0 : 1
                visible: opacity > 0.001
                Behavior on opacity { NumberAnimation { duration: 350 } }
            }

            ChargingScreen {
                anchors.fill: parent
                awake: dash.awake && vehicle.isCharging
                opacity: vehicle.isCharging ? 1 : 0
                visible: opacity > 0.001
                Behavior on opacity { NumberAnimation { duration: 350 } }
            }

            // Elements shared by both screens — identical anchors keep the
            // corners visually continuous across the drive/charge switch.
            InfoCorner {
                anchors.right: parent.right
                anchors.rightMargin: 72
                anchors.top: parent.top
                anchors.topMargin: 44
            }

            Text {   // odometer
                anchors.right: parent.right
                anchors.rightMargin: 72
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 40
                text: Theme.fmtThousands(vehicle.odometer) + " km"
                font.family: Theme.sans
                font.pixelSize: 24
                font.letterSpacing: 0.5
                color: Theme.subtext
            }

            Row {   // data-link indicators, always visible on both screens
                anchors.left: parent.left
                anchors.leftMargin: 72
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 40
                spacing: 26
                LinkBadge {
                    label: "OBD"
                    ok: vehicle.obdState === "connected"
                }
                LinkBadge {
                    label: "AAOS"
                    ok: vehicle.aaosConnected
                }
            }

            BlindSpotGlow { side: -1; active: vehicle.blindLeft }
            BlindSpotGlow { side: 1;  active: vehicle.blindRight }
        }
    }

    // Parking standby: pure black over everything. The supervisor also cuts
    // the backlight when it can; this is the guaranteed software layer.
    Rectangle {
        anchors.fill: parent
        z: 100
        color: "black"
        opacity: vehicle.obdState === "standby" ? 1 : 0
        visible: opacity > 0.001
        Behavior on opacity { NumberAnimation { duration: 800 } }
    }
}
