import QtQuick
import EX30 1.0

// Dominant center element: smoothed speed numerals, unit, gear row.
Item {
    id: cluster
    width: 620
    height: 480

    property real shownSpeed: vehicle.speed
    Behavior on shownSpeed {
        NumberAnimation { duration: 280; easing.type: Easing.OutCubic }
    }

    Text {
        id: speedText
        anchors.horizontalCenter: parent.horizontalCenter
        y: 8
        // Speed is AAOS-only (no OBD fallback lane) — a dead cabin link must
        // blank the numeral, not leave the last value frozen on screen.
        text: vehicle.aaosConnected ? Math.round(cluster.shownSpeed) : "–"
        color: vehicle.aaosConnected ? Theme.text : Theme.faint
        font.family: Theme.sans
        font.pixelSize: 252
        font.weight: Font.Light
        font.features: ({ "tnum": 1 })
        Behavior on color { ColorAnimation { duration: 400 } }
    }

    Text {
        id: unit
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.horizontalCenterOffset: font.letterSpacing / 2
        y: 322
        text: "km/h"
        color: Theme.subtext
        font.family: Theme.sans
        font.pixelSize: 25
        font.letterSpacing: 4
    }

    // Gear row — P R N D with a sliding indicator under the active letter
    Item {
        id: gearRow
        anchors.horizontalCenter: parent.horizontalCenter
        y: 396
        width: gears.width
        height: 56

        readonly property var order: ["P", "R", "N", "D"]
        readonly property int activeIdx: order.indexOf(vehicle.gear)

        Row {
            id: gears
            spacing: 34
            Repeater {
                model: gearRow.order
                Text {
                    required property string modelData
                    required property int index
                    width: 30
                    horizontalAlignment: Text.AlignHCenter
                    text: modelData
                    color: index === gearRow.activeIdx ? Theme.text : Theme.faint
                    font.family: Theme.sans
                    font.pixelSize: 29
                    font.weight: index === gearRow.activeIdx ? Font.DemiBold : Font.Medium
                    Behavior on color { ColorAnimation { duration: 220 } }
                }
            }
        }

        Rectangle {
            id: gearDot
            width: 22
            height: 3.5
            radius: 2
            color: Theme.text
            y: 40
            visible: gearRow.activeIdx >= 0
            x: gearRow.activeIdx >= 0 ? gearRow.activeIdx * (30 + 34) + 4 : 0
            Behavior on x {
                NumberAnimation { duration: 240; easing.type: Easing.OutCubic }
            }
        }
    }
}
