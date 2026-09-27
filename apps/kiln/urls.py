from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("floor/grid/", views.floor_grid_partial, name="floor_grid"),
    path("hearth/<int:pk>/drawer/", views.hearth_drawer, name="hearth_drawer"),
    path("hearth/<int:pk>/phase/", views.change_phase, name="change_phase"),
    path("hearth/<int:pk>/probe/", views.add_probe, name="add_probe"),
    path("hearth/<int:pk>/open-run/", views.open_run, name="open_run"),
    path("hearth/<int:pk>/close-run/", views.close_run, name="close_run"),
    path("hearth/<int:pk>/delete/", views.hearth_delete, name="hearth_delete"),
    path("run/<int:pk>/delete/", views.run_delete, name="run_delete"),
    path("probe/<int:pk>/delete/", views.probe_delete, name="probe_delete"),
    path("resin-lots/", views.resin_lot_feed, name="resin_lot_feed"),
    path(
        "resin-lots/<int:pk>/delete/",
        views.resin_lot_delete,
        name="resin_lot_delete",
    ),
]
