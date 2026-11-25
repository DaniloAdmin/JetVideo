import gc
import tkinter as tk

import bot
from i18n import language, set_language, tr
import publisher_ui
from studio import Studio


def test_history_fits_normal_window_and_preserves_scroll_and_selection(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    publisher_ui.initialize_workspace(workspace)
    monkeypatch.setattr(publisher_ui, "data_home", lambda: tmp_path)
    monkeypatch.setattr(publisher_ui.ModernApp, "_choose_startup_workspace",
                        lambda _self: (workspace, tmp_path / "preferences.json"))
    path = workspace / "state.json"
    filename = "очень длинное название видеозаписи из истории " + "с дополнительным описанием " * 5 + ".mp4"
    records = {str(part): {"filename": f"video-{part}.mp4", "status": "sent"}
               for part in range(1, 41)}
    records["713"] = {"filename": filename, "status": "scheduled", "due_at": "2026-11-20T12:00:00Z"}
    publisher_ui.write_json(path, {"items": records})
    previous_language = language()
    gc.collect()
    window = Studio(locale_code="ru", initial_page="publisher")
    try:
        window.geometry("1080x680")
        window.update()
        publisher = window.publisher
        assert publisher.history_shortcut.winfo_viewable()
        publisher.history_shortcut.invoke()
        window.update()
        tree = publisher.queue_tree
        assert publisher.active_page == "history"
        assert tree.winfo_viewable() and tree.winfo_height() >= 260
        assert tree.bbox("713") and not tree.bbox("1")
        assert tree.xview() == (0.0, 1.0)
        assert all(button.winfo_ismapped() and button.winfo_height() >= button.winfo_reqheight()
                   for button in publisher.nav_buttons.values())
        tree.yview_moveto(1)
        window.update()
        assert tree.bbox("1") and not tree.bbox("713")

        tree.yview_moveto(.45)
        tree.selection_set("21", "20")
        tree.focus("21")
        window.update()
        viewport = tree.yview()
        publisher._refresh_dashboard()
        assert tree.yview() == viewport
        assert set(tree.selection()) == {"21", "20"}
        assert publisher.history_detail_button["state"] == "disabled"
        records["20"]["status"] = "error"
        publisher_ui.write_json(path, {"items": records})
        publisher._refresh_dashboard()
        window.update()
        assert tree.yview() == viewport
        assert tree.focus() == "21"
        assert set(tree.selection()) == {"21", "20"}
        assert tree.set("20", "status") == tr("! Ошибка")

        publisher.history_search.set("нет такой записи")
        window.update()
        assert not tree.get_children()
        assert publisher.history_empty["text"] == tr("Нет совпадений")
        publisher.history_search.set("ДОПОЛНИТЕЛЬНЫМ")
        assert tree.get_children() == ("713",)
        publisher.history_search.set("713")
        assert tree.get_children() == ("713",)
        tree.selection_set("713")
        window.update()
        assert publisher.history_detail_button["state"] == "normal"
        details = publisher.show_history_details()
        assert details is not None
        body = next(child for child in details.winfo_children() if isinstance(child, tk.Frame))
        text = next(child for child in body.winfo_children() if isinstance(child, tk.Text))
        assert filename in text.get("1.0", "end")
        assert tr("◷ Запланировано") in text.get("1.0", "end")
        details.destroy()

        called = []

        def fake_sync(folder, profile_ids, job):
            called.append((folder, profile_ids, job))
            return dict.fromkeys(profile_ids)

        monkeypatch.setattr(publisher_ui.portfolio, "run_profiles", fake_sync)
        publisher.run_job("sync")
        publisher.worker.join(timeout=2)
        assert not publisher.worker.is_alive()
        assert called == [(workspace, [publisher.store.selected().id], "sync")]
        assert publisher_ui.read_json(path, {}) == {"items": records}
    finally:
        bot.set_log_callback(None)
        window.destroy()
        set_language(previous_language)
