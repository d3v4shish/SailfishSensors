Name:           sailfish-sensors-bridge
Summary:        Publish Sailfish SensorFW readings to an authenticated PC relay
Version:         1.0.0
Release:         3
License:         MIT
URL:             https://example.invalid/sailfish-sensors-bridge
Source0:         %{name}-%{version}.tar.bz2
BuildRequires:   pkgconfig(Qt5Core)
BuildRequires:   pkgconfig(Qt5Network)
BuildRequires:   pkgconfig(Qt5Positioning)
BuildRequires:   pkgconfig(Qt5Sensors)

%description
A loopback-only service that reads Sailfish SensorFW and Qt Positioning, then
publishes authenticated sensor and location readings to PC relays.

%prep
%setup -q -n %{name}-%{version}

%build
%qmake5
%make_build

%install
%qmake5_install

%post
/usr/bin/systemctl daemon-reload >/dev/null 2>&1 || :

%postun
/usr/bin/systemctl daemon-reload >/dev/null 2>&1 || :

%files
%defattr(-,root,root,-)
%license LICENSE
/usr/bin/sailfish-sensor-bridge
/usr/lib/systemd/system/sailfish-sensor-bridge.service
