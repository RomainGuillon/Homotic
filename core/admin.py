# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

from django.contrib import admin

from .models import (
    Control,
    DashboardBlock,
    LogEntry,
    Module,
    Scenario,
    Setting,
    Variable,
)


@admin.register(DashboardBlock)
class DashboardBlockAdmin(admin.ModelAdmin):
    list_display = ("key", "order", "width")


@admin.register(Variable)
class VariableAdmin(admin.ModelAdmin):
    list_display = ("name", "value", "description", "updated_at")


@admin.register(Scenario)
class ScenarioAdmin(admin.ModelAdmin):
    list_display = ("name", "trigger_summary", "enabled", "last_run", "last_status")
    list_filter = ("enabled",)


@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ("name", "label", "enabled", "installed_at")
    list_filter = ("enabled",)


@admin.register(Control)
class ControlAdmin(admin.ModelAdmin):
    list_display = ("label", "name", "type", "icon", "group", "is_on", "created_at")
    list_filter = ("type", "group")


@admin.register(LogEntry)
class LogEntryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "module", "level", "message")
    list_filter = ("module", "level")
    search_fields = ("message",)


@admin.register(Setting)
class SettingAdmin(admin.ModelAdmin):
    list_display = ("module", "key", "value", "secret", "updated_at")
    list_filter = ("module",)
    search_fields = ("key", "value")
