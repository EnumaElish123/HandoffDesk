import os
import unittest
from unittest.mock import patch

from handoffdesk import terminal_session


class TerminalSessionTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'HANDOFFDESK_TMUX_READER': '1', 'TMUX': '/tmp/socket,10,0',
                                      'TMUX_PANE': '%1'}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def state(self, *, pane_pid=None, linked=0, windows=1, panes=1, clients=1, tty='/dev/pts/7'):
        return f'{pane_pid or os.getpid()}:{linked}:{windows}:{panes}:{clients}:$0:2357:{tty}'

    def test_only_explicit_reader_offers_detach(self):
        self.assertTrue(terminal_session.is_managed_reader())
        for key in ('HANDOFFDESK_TMUX_READER', 'TMUX', 'TMUX_PANE'):
            with self.subTest(key=key), patch.dict(os.environ, {key: ''}):
                self.assertFalse(terminal_session.is_managed_reader())
                with patch.object(terminal_session, '_tmux') as command, self.assertRaises(ValueError):
                    terminal_session.detach_reader()
                command.assert_not_called()

    def test_foreign_shared_or_ambiguous_panes_do_not_detach(self):
        for settings in [dict(pane_pid=os.getpid()+1), dict(linked=1), dict(windows=2), dict(panes=2),
                         dict(clients=2), dict(clients=0), dict(tty='control-command')]:
            with self.subTest(settings=settings):
                with patch.object(terminal_session, '_tmux', return_value=self.state(**settings)) as command:
                    with self.assertRaises(ValueError):
                        terminal_session.detach_reader()
                    self.assertEqual(command.call_count, 1)

    def test_detach_targets_only_the_observed_terminal_and_rechecks_it(self):
        state = self.state()
        with patch.object(terminal_session, '_tmux', side_effect=[state, '']) as command:
            terminal_session.detach_reader()
        args = command.call_args.args
        self.assertEqual(args[:4], ('if-shell', '-F', '-t', '%1'))
        self.assertIn(state, args[4])
        self.assertEqual(args[5], "detach-client -t '/dev/pts/7'")

    def test_changed_connection_leaves_reader_running(self):
        with patch.object(terminal_session, '_tmux', side_effect=[self.state(), 'changed']):
            with self.assertRaisesRegex(ValueError, '连接刚刚发生变化'):
                terminal_session.detach_reader()
