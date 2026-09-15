/* SPDX-License-Identifier: GPL-2.0 WITH Linux-syscall-note */
#ifndef _UAPI_SAILFISH_IIO_H
#define _UAPI_SAILFISH_IIO_H

#include <linux/types.h>

#define SAILFISH_IIO_ABI_VERSION 1
#define SAILFISH_IIO_VALUE_COUNT 3

enum sailfish_iio_sensor_id {
	SAILFISH_IIO_ACCELEROMETER = 1,
	SAILFISH_IIO_GYROSCOPE = 2,
	SAILFISH_IIO_MAGNETOMETER = 3,
	SAILFISH_IIO_COMPASS = 4,
	SAILFISH_IIO_ORIENTATION = 5,
	SAILFISH_IIO_LIGHT = 6,
	SAILFISH_IIO_PROXIMITY = 7,
	SAILFISH_IIO_PRESSURE = 8,
	SAILFISH_IIO_ROTATION = 9,
};

/*
 * One fixed-size record written to /dev/sailfish-iio.  values are signed raw
 * IIO values; each device's in_*_scale describes their SI conversion.
 */
struct sailfish_iio_sample {
	__u16 version;
	__u16 sensor_id;
	__u16 value_count;
	__u16 reserved;
	__u64 timestamp_ns;
	__s32 values[SAILFISH_IIO_VALUE_COUNT];
} __attribute__((packed));

#endif
