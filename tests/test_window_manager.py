import window_manager as wm


def test_grid_position_first_window_at_origin():
    rect = wm.grid_position(0, columns=5, width=300, height=200)
    assert rect == wm.WindowRect(x=0, y=0, width=300, height=200)


def test_grid_position_wraps_to_next_row():
    rect = wm.grid_position(5, columns=5, width=300, height=200)
    assert rect == wm.WindowRect(x=0, y=200, width=300, height=200)


def test_grid_position_middle_of_row():
    rect = wm.grid_position(7, columns=5, width=300, height=200)
    assert rect == wm.WindowRect(x=600, y=200, width=300, height=200)


def test_grid_position_treats_non_positive_columns_as_one():
    rect = wm.grid_position(3, columns=0, width=100, height=100)
    assert rect == wm.WindowRect(x=0, y=300, width=100, height=100)
