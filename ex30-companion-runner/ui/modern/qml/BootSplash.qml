import QtQuick
import EX30 1.0

// Opening sequence: gapped ring sweeps on, arrow slides through the gap,
// the inner VOLVO wordmark tracks into place. ~3s, then hands off to the
// loading screen.
Item {
    id: splash
    property bool active: false
    signal finished()

    visible: opacity > 0.001
    opacity: active ? 1 : 0
    Behavior on opacity {
        NumberAnimation { duration: 450; easing.type: Easing.InOutQuad }
    }

    onActiveChanged: if (active && !seq.running) seq.restart()
    Component.onCompleted: if (active && !seq.running) seq.restart()

    Item {
        id: markGroup
        anchors.horizontalCenter: parent.horizontalCenter
        // circle center sits at 0.45 of the item (arrow extends NE) —
        // shift right so the CIRCLE, not the bounding box, is centered
        anchors.horizontalCenterOffset: width * 0.05
        anchors.verticalCenter: parent.verticalCenter
        anchors.verticalCenterOffset: -30
        width: 290
        height: 290
        scale: 0.94

        IronMark {
            id: mark
            size: 290
            color: Theme.bootText
            circleSweep: 0
            arrowProgress: 0
            wordmarkProgress: 0
        }
    }

    Text {
        id: subMark
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.horizontalCenterOffset: font.letterSpacing / 2
        y: markGroup.y + markGroup.height + 22
        text: "EX30"
        color: Theme.bootText
        opacity: 0
        font.family: Theme.sans
        font.pixelSize: 17
        font.weight: Font.Normal
        font.letterSpacing: 9
    }

    Signature {
        id: sig
        anchors.right: parent.right
        anchors.rightMargin: 72
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 34
        opacity: 0
    }

    SequentialAnimation {
        id: seq
        running: false

        PauseAnimation { duration: 300 }

        // Ring sweeps on while the whole mark breathes up to full scale
        ParallelAnimation {
            NumberAnimation {
                target: mark; property: "circleSweep"
                from: 0; to: 360; duration: 950
                easing.type: Easing.InOutCubic
            }
            NumberAnimation {
                target: markGroup; property: "scale"
                from: 0.94; to: 1.0; duration: 2300
                easing.type: Easing.OutCubic
            }
        }

        NumberAnimation {
            target: mark; property: "arrowProgress"
            from: 0; to: 1; duration: 420
            easing.type: Easing.OutCubic
        }

        ParallelAnimation {
            NumberAnimation {
                target: mark; property: "wordmarkProgress"
                from: 0; to: 1; duration: 700
                easing.type: Easing.OutCubic
            }
            SequentialAnimation {
                PauseAnimation { duration: 250 }
                ParallelAnimation {
                    NumberAnimation {
                        target: subMark; property: "opacity"
                        from: 0; to: 0.55; duration: 450
                    }
                    NumberAnimation {
                        target: sig; property: "opacity"
                        from: 0; to: 0.42; duration: 450
                    }
                }
            }
        }

        PauseAnimation { duration: 650 }

        ScriptAction { script: splash.finished() }
    }
}
