"""Publish search dispatch and server timestamps without restarting WeChat."""
import deploy_moments_recovery as rollout

rollout.DEPLOYMENT = 'search-clock'
rollout.FILES = {'whalegirl_linux/' + name for name in (
    'runtime.py', 'proactive.py', 'search.py', 'context.py', 'upstream/ai_engine.py', 'time_context.py')}
rollout.FILES |= {'tests/test_search_clock.py', 'tests/test_message_time_context.py', 'tests/test_responses.py'}
rollout.TEST_MODULES = ('test_search_clock', 'test_search.SearchApiTests',
    'test_search.SearchRuntimeTests.test_search_tool_runs_between_fast_model_turns_without_quotes',
    'test_message_time_context', 'test_responses',
    'test_chat_habits.ChatHabitTests.test_followup_tool_keeps_original_burst_and_returns_result_to_model')
rollout.REQUIRE_ONLINE = False

if __name__ == '__main__':
    rollout.main()
