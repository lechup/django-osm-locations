# -*- coding: utf-8 -*-
from django.core.management.base import BaseCommand
from django.db import transaction, connection
from django.utils.text import slugify
from tqdm import tqdm

from .. import Country, CountrySubarea, Place, PlaceSubarea, Street


class Command(BaseCommand):
    help = "Recalculate all denormalized fields for locations objects like CountrySubarea.country."

    DENORMALIZED_MODEL_CLASSES = [Country, CountrySubarea, Place, PlaceSubarea, Street]
    MODEL_CLASSES_TO_UPDATE = DENORMALIZED_MODEL_CLASSES[:4]
    MODEL_CLASSES_TO_CLEAN = DENORMALIZED_MODEL_CLASSES[1:]
    DENORMALIZED_MODEL_CLASSES_TO_UPDATE_DICT = {
        'Country': DENORMALIZED_MODEL_CLASSES[1:],
        'CountrySubarea': DENORMALIZED_MODEL_CLASSES[1:],
        'Place': DENORMALIZED_MODEL_CLASSES[2:],
        'PlaceSubarea': DENORMALIZED_MODEL_CLASSES[3:],
    }

    def add_arguments(self, parser):
        parser.add_argument(
            '-a',
            '--all',
            action='store_true',
            default=False,
            help='Calculate all fields even those that currently have value. By default we select only those locations'
                 'that have all denorm fields unset (len = 0).'
        )

    @classmethod
    def get_sql_for_update_denorm_fields(cls, model_name, field_name, array_value, border_ewkb):
        if model_name == 'street':
            field_and_function = {
                'field': 'lines',
                'function': 'ST_Intersects'
            }
        else:
            field_and_function = {
                'field': 'Coalesce(border, center)',
                'function': 'ST_Within'
            }

        sql = u"""
            UPDATE "locations_{model_name}" SET {field_name} =
            array_cat({field_name}, {array_value}) WHERE
              {ST_Function}({ST_Field}, ST_GeomFromEWKB('\\x{border_ewkb}'))
            ;
        """.format(**{
            'model_name': model_name,
            'field_name': field_name,
            'array_value': array_value,
            'border_ewkb': border_ewkb,
            'ST_Function': field_and_function['function'],
            'ST_Field': field_and_function['field'],
        })
        return sql

    @classmethod
    def get_sql_array_value(cls, obj):
        return u"""ARRAY['{}', '{}']::varchar[]""".format(obj.name.replace(u"'", u"''"), obj.slug)

    @classmethod
    def get_sql_for_clean_denorm_fields(cls, model_name, field_name):
        return """
            UPDATE "locations_{model_name}" SET {field_name} = {field_name}[2:array_upper({field_name}, 1)] WHERE
            {field_name} @> ARRAY[ARRAY['','']]::varchar[];
        """.format(**{
            'model_name': model_name,
            'field_name': field_name
        })

    @classmethod
    @transaction.atomic
    def clean_denorm_fields_after_update(cls):
        """
        Remove dummy empty element {'', ''}, we are adding during cls.clean_denorm_fields_before_update() from all
        denorm fields.
        """
        for model_class_to_clean in cls.MODEL_CLASSES_TO_CLEAN:
            print("{} - cleaning denorm fields after update...".format(model_class_to_clean.__name__))
            for field_name in model_class_to_clean.denorm_fields:
                model_name = model_class_to_clean.__name__.lower()
                sql = cls.get_sql_for_clean_denorm_fields(model_name, field_name)
                with connection.cursor() as cursor:
                    cursor.execute(sql)

    @classmethod
    def update_denorm_fields_for_model_class(cls, model_class_to_update):
        """
        Recaltulate all denorm fields for :param model_class_to_update:. We iterate throught other location models
        defined in `cls.DENORMALIZED_MODEL_CLASSES_TO_UPDATE_DICT` and update proper fields for
        :param model_class_to_update:. So for `Country` model we set `country` field on `[CountrySubarea, Place,
        PlaceSubarea, Street]` models and so on.

        :param model_class_to_update: model class
        """
        objects = model_class_to_update.objects.exclude(border=None).only('name', 'slug', 'border').iterator()
        objects_count = model_class_to_update.objects.exclude(border=None).count()
        for obj in tqdm(objects, total=objects_count, desc=model_class_to_update.__name__):
            model_name = model_class_to_update.__name__
            denormalized_model_classes = tqdm(
                cls.DENORMALIZED_MODEL_CLASSES_TO_UPDATE_DICT[model_name],
                leave=False,
                desc=slugify(
                    u'Updating {}'.format(
                        obj.name
                    )
                )
            )
            for denormalized_model_class in denormalized_model_classes:
                field_name = model_class_to_update.__name__.lower()
                model_name = denormalized_model_class.__name__.lower()
                sql = cls.get_sql_for_update_denorm_fields(
                    model_name,
                    field_name,
                    cls.get_sql_array_value(obj),
                    obj.border.hexewkb
                )
                with connection.cursor() as cursor:
                    cursor.execute(sql)

    @classmethod
    @transaction.atomic
    def clean_denorm_fields_before_update(cls):
        """
        Set dummy empty element {'', ''}, as a default to all denorm fields. We need it during
        cls.update_denorm_fields() because postgres needs element to concat multidimensional arrays.
        """
        for model_class_to_clean in cls.MODEL_CLASSES_TO_CLEAN:
            print("{} - cleaning denorm fields before update...".format(model_class_to_clean.__name__))
            update_dict = {}
            for field_name in model_class_to_clean.denorm_fields:
                update_dict[field_name] = [['', '']]

            if hasattr(model_class_to_clean, 'objects_all'):
                model_class_to_clear_all = model_class_to_clean.objects_all
            else:
                model_class_to_clear_all = model_class_to_clean.objects

            model_class_to_clear_all.all().update(
                **update_dict
            )

    @classmethod
    def update_denorm_fields(cls):
        """
        Recalculate all denormalized fields for locations models.
        """
        for model_class_to_update in cls.MODEL_CLASSES_TO_UPDATE:
            print("{} - updating denorm fields...".format(model_class_to_update.__name__))
            cls.update_denorm_fields_for_model_class(model_class_to_update)

    def handle(self, *args, **options):
        # clear
        self.clean_denorm_fields_before_update()
        # update
        self.update_denorm_fields()
        # clean
        self.clean_denorm_fields_after_update()
