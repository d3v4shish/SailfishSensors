// SPDX-License-Identifier: GPL-2.0
/*
 * Virtual Sailfish sensor IIO devices.
 *
 * This module intentionally contains no transport or JSON parser.  A local,
 * unprivileged relay writes fixed-size, validated records to its misc device.
 */

#include <linux/errno.h>
#include <linux/fs.h>
#include <linux/iio/buffer.h>
#include <linux/iio/iio.h>
#include <linux/iio/kfifo_buf.h>
#include <linux/iio/sysfs.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/slab.h>
#include <linux/uaccess.h>

#include "uapi/sailfish_iio.h"

#define SFS_VECTOR_CHANNEL(_type, _modifier, _index) { \
	.type = (_type), .modified = 1, .channel2 = (_modifier), \
	.scan_index = (_index), .info_mask_separate = BIT(IIO_CHAN_INFO_RAW) | \
		BIT(IIO_CHAN_INFO_SCALE), \
	.scan_type = { .sign = 's', .realbits = 32, .storagebits = 32, \
		.endianness = IIO_CPU }, \
}

#define SFS_SCALAR_CHANNEL(_type, _modifier, _index) { \
	.type = (_type), .modified = (_modifier) != IIO_NO_MOD, \
	.channel2 = (_modifier), .scan_index = (_index), \
	.info_mask_separate = BIT(IIO_CHAN_INFO_RAW) | BIT(IIO_CHAN_INFO_SCALE), \
	.scan_type = { .sign = 's', .realbits = 32, .storagebits = 32, \
		.endianness = IIO_CPU }, \
}

enum sfs_sensor_kind {
	SFS_ACCEL,
	SFS_GYRO,
	SFS_MAGN,
	SFS_COMPASS,
	SFS_ORIENTATION,
	SFS_LIGHT,
	SFS_PROXIMITY,
	SFS_PRESSURE,
	SFS_ROTATION,
	SFS_SENSOR_COUNT,
};

struct sfs_sensor {
	struct iio_dev *indio_dev;
	const char *name;
	u16 protocol_id;
	u8 value_count;
	int scale_type;
	int scale_value;
	struct mutex lock;
	s32 values[SAILFISH_IIO_VALUE_COUNT];
	u64 timestamp_ns;
	bool available;
};

static const struct iio_chan_spec sfs_accel_channels[] = {
	SFS_VECTOR_CHANNEL(IIO_ACCEL, IIO_MOD_X, 0),
	SFS_VECTOR_CHANNEL(IIO_ACCEL, IIO_MOD_Y, 1),
	SFS_VECTOR_CHANNEL(IIO_ACCEL, IIO_MOD_Z, 2),
	IIO_CHAN_SOFT_TIMESTAMP(3),
};

static const struct iio_chan_spec sfs_gyro_channels[] = {
	SFS_VECTOR_CHANNEL(IIO_ANGL_VEL, IIO_MOD_X, 0),
	SFS_VECTOR_CHANNEL(IIO_ANGL_VEL, IIO_MOD_Y, 1),
	SFS_VECTOR_CHANNEL(IIO_ANGL_VEL, IIO_MOD_Z, 2),
	IIO_CHAN_SOFT_TIMESTAMP(3),
};

static const struct iio_chan_spec sfs_magn_channels[] = {
	SFS_VECTOR_CHANNEL(IIO_MAGN, IIO_MOD_X, 0),
	SFS_VECTOR_CHANNEL(IIO_MAGN, IIO_MOD_Y, 1),
	SFS_VECTOR_CHANNEL(IIO_MAGN, IIO_MOD_Z, 2),
	IIO_CHAN_SOFT_TIMESTAMP(3),
};

static const struct iio_chan_spec sfs_compass_channels[] = {
	SFS_SCALAR_CHANNEL(IIO_ROT, IIO_MOD_NORTH_MAGN_TILT_COMP, 0),
	IIO_CHAN_SOFT_TIMESTAMP(1),
};

static const struct iio_chan_spec sfs_orientation_channels[] = {
	SFS_SCALAR_CHANNEL(IIO_ANGL, IIO_NO_MOD, 0),
	IIO_CHAN_SOFT_TIMESTAMP(1),
};

static const struct iio_chan_spec sfs_light_channels[] = {
	SFS_SCALAR_CHANNEL(IIO_LIGHT, IIO_NO_MOD, 0),
	IIO_CHAN_SOFT_TIMESTAMP(1),
};

static const struct iio_chan_spec sfs_proximity_channels[] = {
	SFS_SCALAR_CHANNEL(IIO_PROXIMITY, IIO_NO_MOD, 0),
	IIO_CHAN_SOFT_TIMESTAMP(1),
};

static const struct iio_chan_spec sfs_pressure_channels[] = {
	SFS_SCALAR_CHANNEL(IIO_PRESSURE, IIO_NO_MOD, 0),
	IIO_CHAN_SOFT_TIMESTAMP(1),
};

static const struct iio_chan_spec sfs_rotation_channels[] = {
	SFS_VECTOR_CHANNEL(IIO_ROT, IIO_MOD_X, 0),
	SFS_VECTOR_CHANNEL(IIO_ROT, IIO_MOD_Y, 1),
	SFS_VECTOR_CHANNEL(IIO_ROT, IIO_MOD_Z, 2),
	IIO_CHAN_SOFT_TIMESTAMP(3),
};

struct sfs_sensor_definition {
	const char *name;
	u16 protocol_id;
	u8 value_count;
	int scale_type;
	int scale_value;
	const struct iio_chan_spec *channels;
	int channel_count;
};

static const struct sfs_sensor_definition sfs_definitions[SFS_SENSOR_COUNT] = {
	[SFS_ACCEL] = { "sailfish-accelerometer", SAILFISH_IIO_ACCELEROMETER, 3, IIO_VAL_INT_PLUS_MICRO, 1,
		sfs_accel_channels, ARRAY_SIZE(sfs_accel_channels) },
	[SFS_GYRO] = { "sailfish-gyroscope", SAILFISH_IIO_GYROSCOPE, 3, IIO_VAL_INT_PLUS_MICRO, 1,
		sfs_gyro_channels, ARRAY_SIZE(sfs_gyro_channels) },
	[SFS_MAGN] = { "sailfish-magnetometer", SAILFISH_IIO_MAGNETOMETER, 3, IIO_VAL_INT_PLUS_NANO, 1,
		sfs_magn_channels, ARRAY_SIZE(sfs_magn_channels) },
	[SFS_COMPASS] = { "sailfish-compass", SAILFISH_IIO_COMPASS, 1, IIO_VAL_INT_PLUS_MICRO, 1,
		sfs_compass_channels, ARRAY_SIZE(sfs_compass_channels) },
	[SFS_ORIENTATION] = { "sailfish-orientation", SAILFISH_IIO_ORIENTATION, 1, IIO_VAL_INT, 1,
		sfs_orientation_channels, ARRAY_SIZE(sfs_orientation_channels) },
	[SFS_LIGHT] = { "sailfish-light", SAILFISH_IIO_LIGHT, 1, IIO_VAL_INT_PLUS_MICRO, 1000,
		sfs_light_channels, ARRAY_SIZE(sfs_light_channels) },
	[SFS_PROXIMITY] = { "sailfish-proximity", SAILFISH_IIO_PROXIMITY, 1, IIO_VAL_INT, 1,
		sfs_proximity_channels, ARRAY_SIZE(sfs_proximity_channels) },
	[SFS_PRESSURE] = { "sailfish-pressure", SAILFISH_IIO_PRESSURE, 1, IIO_VAL_INT_PLUS_MICRO, 1000,
		sfs_pressure_channels, ARRAY_SIZE(sfs_pressure_channels) },
	[SFS_ROTATION] = { "sailfish-rotation", SAILFISH_IIO_ROTATION, 3, IIO_VAL_INT_PLUS_MICRO, 1,
		sfs_rotation_channels, ARRAY_SIZE(sfs_rotation_channels) },
};

static struct sfs_sensor *sfs_sensors[SFS_SENSOR_COUNT];

static ssize_t sfs_show_available(struct device *dev,
				  struct device_attribute *attr, char *buf)
{
	struct iio_dev *indio_dev = dev_to_iio_dev(dev);
	struct sfs_sensor *sensor = iio_priv(indio_dev);
	bool available;

	mutex_lock(&sensor->lock);
	available = sensor->available;
	mutex_unlock(&sensor->lock);
	return sysfs_emit(buf, "%d\n", available);
}

static ssize_t sfs_show_timestamp_ns(struct device *dev,
				     struct device_attribute *attr, char *buf)
{
	struct iio_dev *indio_dev = dev_to_iio_dev(dev);
	struct sfs_sensor *sensor = iio_priv(indio_dev);
	u64 timestamp_ns;

	mutex_lock(&sensor->lock);
	timestamp_ns = sensor->timestamp_ns;
	mutex_unlock(&sensor->lock);
	return sysfs_emit(buf, "%llu\n", timestamp_ns);
}

static IIO_DEVICE_ATTR(sailfish_available, 0444, sfs_show_available, NULL, 0);
static IIO_DEVICE_ATTR(sailfish_timestamp_ns, 0444, sfs_show_timestamp_ns, NULL, 0);

static struct attribute *sfs_attributes[] = {
	&iio_dev_attr_sailfish_available.dev_attr.attr,
	&iio_dev_attr_sailfish_timestamp_ns.dev_attr.attr,
	NULL,
};

static const struct attribute_group sfs_attribute_group = {
	.attrs = sfs_attributes,
};

static int sfs_read_raw(struct iio_dev *indio_dev,
			const struct iio_chan_spec *chan, int *val, int *val2, long mask)
{
	struct sfs_sensor *sensor = iio_priv(indio_dev);

	switch (mask) {
	case IIO_CHAN_INFO_RAW:
		mutex_lock(&sensor->lock);
		if (!sensor->available) {
			mutex_unlock(&sensor->lock);
			return -ENODATA;
		}
		*val = sensor->values[chan->scan_index];
		mutex_unlock(&sensor->lock);
		return IIO_VAL_INT;
	case IIO_CHAN_INFO_SCALE:
		if (sensor->scale_type == IIO_VAL_INT) {
			*val = sensor->scale_value;
			return IIO_VAL_INT;
		}
		*val = 0;
		*val2 = sensor->scale_value;
		return sensor->scale_type;
	default:
		return -EINVAL;
	}
}

static const struct iio_info sfs_iio_info = {
	.read_raw = sfs_read_raw,
	.attrs = &sfs_attribute_group,
};

static struct sfs_sensor *sfs_sensor_for_id(u16 protocol_id)
{
	int index;

	for (index = 0; index < SFS_SENSOR_COUNT; ++index) {
		if (sfs_sensors[index] && sfs_sensors[index]->protocol_id == protocol_id)
			return sfs_sensors[index];
	}
	return NULL;
}

static ssize_t sfs_inject_write(struct file *file, const char __user *buffer,
				size_t count, loff_t *offset)
{
	struct sailfish_iio_sample sample;
	struct sfs_sensor *sensor;
	u8 buffer_data[32] __aligned(8) = {};
	int result;

	if (count != sizeof(sample))
		return -EINVAL;
	if (copy_from_user(&sample, buffer, sizeof(sample)))
		return -EFAULT;
	if (sample.version != SAILFISH_IIO_ABI_VERSION || sample.reserved != 0 ||
	    sample.timestamp_ns == 0)
		return -EINVAL;
	sensor = sfs_sensor_for_id(sample.sensor_id);
	if (!sensor || sample.value_count != sensor->value_count)
		return -EINVAL;

	mutex_lock(&sensor->lock);
	memcpy(sensor->values, sample.values, sizeof(sensor->values));
	sensor->timestamp_ns = sample.timestamp_ns;
	sensor->available = true;
	mutex_unlock(&sensor->lock);
	memcpy(buffer_data, sample.values, sizeof(sample.values));

	result = iio_push_to_buffers_with_ts(sensor->indio_dev, buffer_data,
					     sizeof(buffer_data),
					     sample.timestamp_ns);
	if (result && result != -EBUSY)
		return result;
	return sizeof(sample);
}

static const struct file_operations sfs_inject_fops = {
	.owner = THIS_MODULE,
	.write = sfs_inject_write,
	.llseek = noop_llseek,
};

static struct miscdevice sfs_inject_device = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "sailfish-iio",
	.fops = &sfs_inject_fops,
	.mode = 0660,
};

static int sfs_register_sensor(enum sfs_sensor_kind kind)
{
	const struct sfs_sensor_definition *definition = &sfs_definitions[kind];
	struct iio_dev *indio_dev;
	struct sfs_sensor *sensor;
	int result;

	indio_dev = iio_device_alloc(NULL, sizeof(*sensor));
	if (!indio_dev)
		return -ENOMEM;
	sensor = iio_priv(indio_dev);
	sensor->indio_dev = indio_dev;
	sensor->name = definition->name;
	sensor->protocol_id = definition->protocol_id;
	sensor->value_count = definition->value_count;
	sensor->scale_type = definition->scale_type;
	sensor->scale_value = definition->scale_value;
	mutex_init(&sensor->lock);

	indio_dev->name = definition->name;
	indio_dev->info = &sfs_iio_info;
	indio_dev->modes = INDIO_DIRECT_MODE | INDIO_BUFFER_SOFTWARE;
	indio_dev->channels = definition->channels;
	indio_dev->num_channels = definition->channel_count;

	result = devm_iio_kfifo_buffer_setup(&indio_dev->dev, indio_dev, NULL);
	if (result)
		goto free_device;
	result = iio_device_register(indio_dev);
	if (result)
		goto free_device;
	sfs_sensors[kind] = sensor;
	return 0;

free_device:
	iio_device_free(indio_dev);
	return result;
}

static void sfs_unregister_sensors(void)
{
	int index;

	for (index = SFS_SENSOR_COUNT - 1; index >= 0; --index) {
		if (!sfs_sensors[index])
			continue;
		iio_device_unregister(sfs_sensors[index]->indio_dev);
		iio_device_free(sfs_sensors[index]->indio_dev);
		sfs_sensors[index] = NULL;
	}
}

static int __init sfs_init(void)
{
	int index;
	int result;

	BUILD_BUG_ON(sizeof(struct sailfish_iio_sample) != 28);
	result = misc_register(&sfs_inject_device);
	if (result)
		return result;
	for (index = 0; index < SFS_SENSOR_COUNT; ++index) {
		result = sfs_register_sensor(index);
		if (result) {
			sfs_unregister_sensors();
			misc_deregister(&sfs_inject_device);
			return result;
		}
	}
	pr_info("sailfish_iio: registered virtual Sailfish sensor devices\n");
	return 0;
}

static void __exit sfs_exit(void)
{
	sfs_unregister_sensors();
	misc_deregister(&sfs_inject_device);
}

module_init(sfs_init);
module_exit(sfs_exit);

MODULE_AUTHOR("Sailfish Sensors Bridge contributors");
MODULE_DESCRIPTION("Inject Sailfish phone sensor readings into virtual IIO devices");
MODULE_LICENSE("GPL");
