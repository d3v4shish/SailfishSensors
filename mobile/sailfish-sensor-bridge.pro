QT += core network positioning sensors
CONFIG += console c++11
CONFIG -= app_bundle
TEMPLATE = app
TARGET = sailfish-sensor-bridge
SOURCES += sailfish-sensor-bridge.cpp

target.path = /usr/bin
service.files = systemd/sailfish-sensor-bridge.service
service.path = /usr/lib/systemd/system
INSTALLS += target service

DISTFILES += \
    LICENSE \
    systemd/sailfish-sensor-bridge.service \
    rpm/sailfish-sensors-bridge.spec \
    rpm/sailfish-sensors-bridge.changes
