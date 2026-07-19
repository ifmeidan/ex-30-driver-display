import QtQuick
import EX30 1.0

// Vertical pedal gauge that fills bottom-up as stacked tick segments
// (13, Polestar-style).
Item {
    id: bar
    property real value: 0
    property color fillColor: Theme.red
    property string label: ""

    readonly property bool active: shown > 0.02
    property real shown: value
    Behavior on shown { NumberAnimation { duration: 110 } }

    width: 120
    height: 480

    readonly property real trackY: 106
    readonly property real trackH: 244

    // ── Segmented ticks (13, bottom-up) ──────────────────────────
    Item {
        anchors.fill: parent

        readonly property int count: 13
        readonly property real segH: 12
        readonly property real gap: (bar.trackH - 13 * 12) / 12

        Repeater {
            model: 13
            Rectangle {
                required property int index
                readonly property real frac:
                    Math.max(0, Math.min(1, bar.shown * 13 - index))
                anchors.horizontalCenter: parent.horizontalCenter
                y: bar.trackY + bar.trackH - (index + 1) * 12 - index * parent.gap
                width: 18
                height: 12
                radius: 3.5
                color: frac > 0 ? bar.fillColor : Theme.faint
                opacity: frac > 0 ? 0.30 + 0.70 * frac : 0.30
            }
        }
    }

    Text {
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.horizontalCenterOffset: font.letterSpacing / 2
        y: bar.trackY + bar.trackH + 20
        text: bar.label
        color: bar.active ? bar.fillColor : Theme.subtext
        opacity: bar.active ? 1 : 0.55
        font.family: Theme.sans
        font.pixelSize: 15
        font.weight: Font.DemiBold
        font.letterSpacing: 2.5
        Behavior on color { ColorAnimation { duration: 200 } }
    }
}
