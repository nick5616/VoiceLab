"""The shell's screens (dashboard, live, practice, settings), plus spec 06's take, report and progress
widgets they embed, imported here so the screens share one place for them."""
from ui.progress.progress_screen import ProgressMini, ProgressScreen
from ui.takes.latest_take import LatestTakePanel
from ui.takes.recent_list import RecentTakes
from ui.takes.take_card import TakeCard
from ui.takes.takes_screen import TakesScreen
from ui.takes.targets_editor import TargetsEditor

__all__ = ["LatestTakePanel", "ProgressMini", "ProgressScreen", "RecentTakes", "TakeCard", "TakesScreen",
           "TargetsEditor"]
