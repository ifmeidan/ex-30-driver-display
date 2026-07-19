import QtQuick
import EX30 1.0

// Charging layout: big SoC + live power on the left, curve center-right.
Item {
    id: screen
    property bool awake: false

    property real shownSoc: vehicle.socPct
    property real shownKw: vehicle.chargePowerKw
    Behavior on shownSoc { NumberAnimation { duration: 400 } }
    Behavior on shownKw  { NumberAnimation { duration: 300 } }

    readonly property bool flowing: shownKw > 0.3

    transform: Translate {
        y: screen.awake ? 0 : 12
        Behavior on y { NumberAnimation { duration: 600; easing.type: Easing.OutCubic } }
    }

    // ── Left cluster ─────────────────────────────────────────────
    Item {
        x: 72
        y: 52
        width: 420

        Row {
            id: caption
            spacing: 12

            Rectangle {
                width: 10
                height: 10
                radius: 5
                anchors.verticalCenter: parent.verticalCenter
                color: Theme.green
                visible: screen.flowing
                SequentialAnimation on opacity {
                    running: screen.flowing
                    loops: Animation.Infinite
                    NumberAnimation { from: 1; to: 0.25; duration: 900; easing.type: Easing.InOutSine }
                    NumberAnimation { from: 0.25; to: 1; duration: 900; easing.type: Easing.InOutSine }
                }
            }
            Text {
                text: "CHARGING"
                color: Theme.subtext
                font.family: Theme.sans
                font.pixelSize: 15
                font.weight: Font.DemiBold
                font.letterSpacing: Theme.trackingCaps
            }
        }

        Item {
            id: socRow
            y: 40
            height: 150

            Text {
                id: socText
                anchors.baseline: parent.bottom
                text: screen.shownSoc.toFixed(1)
                color: Theme.text
                font.family: Theme.sans
                font.pixelSize: 140
                font.weight: Font.Light
                font.features: ({ "tnum": 1 })
                Behavior on color { ColorAnimation { duration: 400 } }
            }
            Text {
                anchors.left: socText.right
                anchors.leftMargin: 10
                anchors.baseline: socText.baseline
                text: "%"
                color: Theme.subtext
                font.family: Theme.sans
                font.pixelSize: 52
                font.weight: Font.Light
            }
        }

        // Charge bar with flowing shimmer
        Item {
            id: chargeBar
            y: 212
            width: 380
            height: 10

            Rectangle {
                anchors.fill: parent
                radius: 5
                color: Theme.faint
                opacity: 0.6
            }
            Rectangle {
                id: fill
                width: parent.width * Math.max(0, Math.min(1, screen.shownSoc / 100))
                height: parent.height
                radius: 5
                color: Theme.green
                clip: true

                Rectangle {
                    id: shimmer
                    width: 90
                    height: parent.height
                    visible: screen.flowing
                    gradient: Gradient {
                        orientation: Gradient.Horizontal
                        GradientStop { position: 0; color: "transparent" }
                        GradientStop { position: 0.5; color: "#59FFFFFF" }
                        GradientStop { position: 1; color: "transparent" }
                    }
                    NumberAnimation on x {
                        running: screen.flowing && fill.width > 0
                        loops: Animation.Infinite
                        from: -90
                        to: 380
                        duration: 2000
                    }
                }
            }
        }

        // Live charge power
        Item {
            y: 250
            height: 56

            Text {
                id: kwText
                anchors.baseline: parent.bottom
                text: "+ " + (screen.shownKw >= 99.5
                              ? Math.round(screen.shownKw)
                              : screen.shownKw.toFixed(1))
                color: screen.flowing ? Theme.green : Theme.subtext
                font.family: Theme.sans
                font.pixelSize: 50
                font.weight: Font.Medium
                font.features: ({ "tnum": 1 })
                Behavior on color { ColorAnimation { duration: 300 } }
            }
            Text {
                anchors.left: kwText.right
                anchors.leftMargin: 10
                anchors.baseline: kwText.baseline
                text: "kW"
                color: Theme.subtext
                font.family: Theme.sans
                font.pixelSize: 24
            }
        }

        Row {
            y: 326
            spacing: 10

            Rectangle {
                width: 9
                height: 9
                radius: 4.5
                anchors.verticalCenter: parent.verticalCenter
                color: vehicle.batteryTemp >= 42 ? Theme.red
                     : vehicle.batteryTemp >= 35 ? Theme.amber
                     : Theme.green
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: Math.round(vehicle.batteryTemp) + "°C battery"
                color: Theme.subtext
                font.family: Theme.sans
                font.pixelSize: 21
            }
        }
    }

    ChargeCurve {
        x: 620
        y: 74
    }
}
