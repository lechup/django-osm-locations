# -*- coding: utf-8 -*-
from django.db import models
from django.contrib.postgres.fields import ArrayField


def DenormArrayField(*args, **kwargs):
    return ArrayField(
        ArrayField(
            models.CharField(*args, **kwargs),
            size=2,
            default=list
        ),
        default=list
    )
