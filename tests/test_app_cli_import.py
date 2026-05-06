def test_app_module_import_does_not_require_gui_libraries():
    import sound_vault.app as app

    assert callable(app.main)
