orchestrator issues:
1. after a brief is created or when a enriching is done, and we are asked for review. there inly one option, that is to aprove. no chat back for another round. or manual edit the goals, task etc.
2. no manual edits on on task, or goal aswell after aproval. should have at least one pause/resume button for edits, to chat a new plan or edit goals flow. or manualy  editing a goal, deleting tasks, rewriting connections. etc
3. the agent console is too simples, no color diferration from errors and normal agent logs, no task agent logging history to see when click on it, in he agent console.
4.after one task fail, the next action go to the next task, it should be blocked because one depends on the other, so after all atempts in the task its failed, should pause the system. that way we can manually edit aswell. you see we need a pause/resume button.
5. after everthing fails, the system is locked and canty manually retry, why i cant see a manual retry? it can be the same pause and resume button, when resume form a failing it should retry.
6. the main point of faiulure was rate limit. elaborate a plan to have teletry or enhance the one we have for global metrics, as how many calls we did, how much tookesn per call, etc...

check the design choices from plan and see if drift much from the system design.
logs:
agent console:
10:31:59 AM 2efa403c a1#0 agent.started {"runtime":"pi","cwd":"/tmp/task-2efa403c-0a5c-4a3d-afa2-79a80a464781-a1-n_gzrujn"}
10:32:18 AM 2efa403c a1#1 agent.failed {"kind":"tool_error","reason":"pi exited 1: 400 Provider returned error\n{\"status\":400,\"title\":\"Bad Request\",\"detail\":\"Function id '948fe171-ce7a-4332-8bc0-5e14e90259f9': DEGRADED function cannot be invoked\"}"}
10:32:18 AM 0e76c902 a1#0 agent.started {"runtime":"pi","cwd":"/tmp/task-0e76c902-9bf8-42ff-8e51-3c072571ce37-a1-gmo2lcpg"}
10:33:02 AM 0e76c902 a1#1 agent.finished {"elapsed_seconds":"43.43"}
10:33:02 AM 2efa403c a2#0 agent.started {"runtime":"pi","cwd":"/tmp/task-2efa403c-0a5c-4a3d-afa2-79a80a464781-a2-jvk7lavu"}
10:33:17 AM 2efa403c a2#1 agent.finished {"elapsed_seconds":"14.75"}
10:33:17 AM 5cf8f523 a1#0 agent.started {"runtime":"pi","cwd":"/tmp/task-5cf8f523-e599-4b9e-8d19-b26265c31bba-a1-3mfvw5_k"}
10:33:33 AM 5cf8f523 a1#1 agent.finished {"elapsed_seconds":"16.05"}
10:33:33 AM 13b72a3f a1#0 agent.started {"runtime":"pi","cwd":"/tmp/task-13b72a3f-87c8-453a-a637-f3912303bbf2-a1-a0z26pv9"}
10:34:08 AM 13b72a3f a1#1 agent.finished {"elapsed_seconds":"34.83"}
10:34:08 AM 1725d906 a1#0 agent.started {"runtime":"pi","cwd":"/tmp/task-1725d906-c345-414a-a25a-7c379ba1a1b9-a1-faiig5fi"}
10:34:21 AM 1725d906 a1#1 agent.finished {"elapsed_seconds":"12.93"}
10:34:21 AM 4ef73de7 a1#0 agent.started {"runtime":"pi","cwd":"/tmp/task-4ef73de7-6545-47fb-91c7-4449777c1a18-a1-n45643zr"}
10:34:49 AM 4ef73de7 a1#1 agent.finished {"elapsed_seconds":"28.22"}
10:34:49 AM 0a9e4988 a1#0 agent.started {"runtime":"pi","cwd":"/tmp/task-0a9e4988-bc85-4db6-8c88-e013b95a1063-a1-0wuzjpyp"}
10:35:15 AM 0a9e4988 a1#1 agent.finished {"elapsed_seconds":"26.09"}
10:35:15 AM f912241a a1#0 agent.started {"runtime":"pi","cwd":"/tmp/task-f912241a-8e64-453e-bfaf-81d62e62b492-a1-89f1krwq"}
10:35:35 AM f912241a a1#1 agent.failed {"kind":"rate_limit","reason":"pi exited 1: 429 Rate limit exceeded: free-models-per-day. Add 5 credits to unlock 1000 free model requests per day"}
10:35:35 AM 04e2444b a1#0 agent.started {"runtime":"pi","cwd":"/tmp/task-04e2444b-1b2a-43f2-8005-3d300c631a75-a1-6jku75jb"}
10:35:53 AM 04e2444b a1#1 agent.failed {"kind":"rate_limit","reason":"pi exited 1: 429 Rate limit exceeded: free-models-per-day. Add 5 credits to unlock 1000 free model requests per day"}
10:35:53 AM f912241a a2#0 agent.started {"runtime":"pi","cwd":"/tmp/task-f912241a-8e64-453e-bfaf-81d62e62b492-a2-w5pxkjql"}
10:36:10 AM f912241a a2#1 agent.failed {"kind":"rate_limit","reason":"pi exited 1: 429 Rate limit exceeded: free-models-per-day. Add 5 credits to unlock 1000 free model requests per day"}
10:36:10 AM 04e2444b a2#0 agent.started {"runtime":"pi","cwd":"/tmp/task-04e2444b-1b2a-43f2-8005-3d300c631a75-a2-r427vsgp"}
10:36:28 AM 04e2444b a2#1 agent.failed {"kind":"rate_limit","reason":"pi exited 1: 429 Rate limit exceeded: free-models-per-day. Add 5 credits to unlock 1000 free model requests per day"}
10:36:28 AM f912241a a3#0 agent.started {"runtime":"pi","cwd":"/tmp/task-f912241a-8e64-453e-bfaf-81d62e62b492-a3-8s5p9f52"}
10:36:45 AM f912241a a3#1 agent.failed {"kind":"rate_limit","reason":"pi exited 1: 429 Rate limit exceeded: free-models-per-day. Add 5 credits to unlock 1000 free model requests per day"}
10:36:45 AM 04e2444b a3#0 agent.started {"runtime":"pi","cwd":"/tmp/task-04e2444b-1b2a-43f2-8005-3d300c631a75-a3-w3oygndd"}
10:37:03 AM 04e2444b a3#1 agent.failed {"kind":"rate_limit","reason":"pi exited 1: 429 Rate limit exceeded: free-models-per-day. Add 5 credits to unlock 1000 free model requests per day"}

events:
30m ago
PhaseAdvanced
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b from_phase=awaiting_review to_phase=running
30m ago
TaskStarted
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=1fc45318-b56d-482d-97bd-553969356d60 task_id=2efa403c-0a5c-4a3d-afa2-79a80a464781 attempt=1
30m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=2efa403c-0a5c-4a3d-afa2-79a80a464781 attempt=1 seq=0 type=agent.started payload={"runtime":"pi","cwd":"/tmp/task-2efa403c-0a5c-4a3d-afa2-79a80a464781-a1-n_gzrujn"}
30m ago
TaskRequeued
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=1fc45318-b56d-482d-97bd-553969356d60 task_id=2efa403c-0a5c-4a3d-afa2-79a80a464781 attempt=1 reason=pi exited 1: 400 Provider returned error
{"status":400,"title":"Bad Request","detail":"Function id '948fe171-ce7a-4332-8bc0-5e14e90259f9': DEGRADED function cannot be invoked"}
30m ago
TaskStarted
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=1fc45318-b56d-482d-97bd-553969356d60 task_id=0e76c902-9bf8-42ff-8e51-3c072571ce37 attempt=1
30m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=2efa403c-0a5c-4a3d-afa2-79a80a464781 attempt=1 seq=1 type=agent.failed payload={"kind":"tool_error","reason":"pi exited 1: 400 Provider returned error\n{\"status\":400,\"title\":\"Bad Request\",\"detail\":\"Function id '948fe171-ce7a-4332-8bc0-5e14e90259f9': DEGRADED function cannot be invoked\"}"}
30m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=0e76c902-9bf8-42ff-8e51-3c072571ce37 attempt=1 seq=0 type=agent.started payload={"runtime":"pi","cwd":"/tmp/task-0e76c902-9bf8-42ff-8e51-3c072571ce37-a1-gmo2lcpg"}
29m ago
TaskCompleted
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=1fc45318-b56d-482d-97bd-553969356d60 task_id=0e76c902-9bf8-42ff-8e51-3c072571ce37
29m ago
TaskStarted
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=1fc45318-b56d-482d-97bd-553969356d60 task_id=2efa403c-0a5c-4a3d-afa2-79a80a464781 attempt=2
29m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=0e76c902-9bf8-42ff-8e51-3c072571ce37 attempt=1 seq=1 type=agent.finished payload={"elapsed_seconds":"43.43"}
29m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=2efa403c-0a5c-4a3d-afa2-79a80a464781 attempt=2 seq=0 type=agent.started payload={"runtime":"pi","cwd":"/tmp/task-2efa403c-0a5c-4a3d-afa2-79a80a464781-a2-jvk7lavu"}
29m ago
TaskCompleted
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=1fc45318-b56d-482d-97bd-553969356d60 task_id=2efa403c-0a5c-4a3d-afa2-79a80a464781
29m ago
TaskStarted
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=1fc45318-b56d-482d-97bd-553969356d60 task_id=5cf8f523-e599-4b9e-8d19-b26265c31bba attempt=1
29m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=2efa403c-0a5c-4a3d-afa2-79a80a464781 attempt=2 seq=1 type=agent.finished payload={"elapsed_seconds":"14.75"}
29m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=5cf8f523-e599-4b9e-8d19-b26265c31bba attempt=1 seq=0 type=agent.started payload={"runtime":"pi","cwd":"/tmp/task-5cf8f523-e599-4b9e-8d19-b26265c31bba-a1-3mfvw5_k"}
29m ago
TaskCompleted
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=1fc45318-b56d-482d-97bd-553969356d60 task_id=5cf8f523-e599-4b9e-8d19-b26265c31bba
29m ago
TaskStarted
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=1fc45318-b56d-482d-97bd-553969356d60 task_id=13b72a3f-87c8-453a-a637-f3912303bbf2 attempt=1
29m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=5cf8f523-e599-4b9e-8d19-b26265c31bba attempt=1 seq=1 type=agent.finished payload={"elapsed_seconds":"16.05"}
29m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=13b72a3f-87c8-453a-a637-f3912303bbf2 attempt=1 seq=0 type=agent.started payload={"runtime":"pi","cwd":"/tmp/task-13b72a3f-87c8-453a-a637-f3912303bbf2-a1-a0z26pv9"}
28m ago
TaskCompleted
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=1fc45318-b56d-482d-97bd-553969356d60 task_id=13b72a3f-87c8-453a-a637-f3912303bbf2
28m ago
GoalCompleted
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=1fc45318-b56d-482d-97bd-553969356d60
28m ago
TaskStarted
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=fe3d3eec-d1b9-4143-8c9a-34d217780aaa task_id=1725d906-c345-414a-a25a-7c379ba1a1b9 attempt=1
28m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=13b72a3f-87c8-453a-a637-f3912303bbf2 attempt=1 seq=1 type=agent.finished payload={"elapsed_seconds":"34.83"}
28m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=1725d906-c345-414a-a25a-7c379ba1a1b9 attempt=1 seq=0 type=agent.started payload={"runtime":"pi","cwd":"/tmp/task-1725d906-c345-414a-a25a-7c379ba1a1b9-a1-faiig5fi"}
28m ago
TaskCompleted
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=fe3d3eec-d1b9-4143-8c9a-34d217780aaa task_id=1725d906-c345-414a-a25a-7c379ba1a1b9
28m ago
TaskStarted
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=fe3d3eec-d1b9-4143-8c9a-34d217780aaa task_id=4ef73de7-6545-47fb-91c7-4449777c1a18 attempt=1
28m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=1725d906-c345-414a-a25a-7c379ba1a1b9 attempt=1 seq=1 type=agent.finished payload={"elapsed_seconds":"12.93"}
28m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=4ef73de7-6545-47fb-91c7-4449777c1a18 attempt=1 seq=0 type=agent.started payload={"runtime":"pi","cwd":"/tmp/task-4ef73de7-6545-47fb-91c7-4449777c1a18-a1-n45643zr"}
27m ago
TaskCompleted
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=fe3d3eec-d1b9-4143-8c9a-34d217780aaa task_id=4ef73de7-6545-47fb-91c7-4449777c1a18
27m ago
GoalCompleted
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=fe3d3eec-d1b9-4143-8c9a-34d217780aaa
27m ago
TaskStarted
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=cc7bdce5-ee71-4c85-ba74-02be57773715 task_id=0a9e4988-bc85-4db6-8c88-e013b95a1063 attempt=1
27m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=4ef73de7-6545-47fb-91c7-4449777c1a18 attempt=1 seq=1 type=agent.finished payload={"elapsed_seconds":"28.22"}
27m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=0a9e4988-bc85-4db6-8c88-e013b95a1063 attempt=1 seq=0 type=agent.started payload={"runtime":"pi","cwd":"/tmp/task-0a9e4988-bc85-4db6-8c88-e013b95a1063-a1-0wuzjpyp"}
27m ago
TaskCompleted
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=cc7bdce5-ee71-4c85-ba74-02be57773715 task_id=0a9e4988-bc85-4db6-8c88-e013b95a1063
27m ago
TaskStarted
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=cc7bdce5-ee71-4c85-ba74-02be57773715 task_id=f912241a-8e64-453e-bfaf-81d62e62b492 attempt=1
27m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=0a9e4988-bc85-4db6-8c88-e013b95a1063 attempt=1 seq=1 type=agent.finished payload={"elapsed_seconds":"26.09"}
27m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=f912241a-8e64-453e-bfaf-81d62e62b492 attempt=1 seq=0 type=agent.started payload={"runtime":"pi","cwd":"/tmp/task-f912241a-8e64-453e-bfaf-81d62e62b492-a1-89f1krwq"}
27m ago
TaskRequeued
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=cc7bdce5-ee71-4c85-ba74-02be57773715 task_id=f912241a-8e64-453e-bfaf-81d62e62b492 attempt=1 reason=pi exited 1: 429 Rate limit exceeded: free-models-per-day. Add 5 credits to unlock 1000 free model requests per day
27m ago
TaskStarted
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=cc7bdce5-ee71-4c85-ba74-02be57773715 task_id=04e2444b-1b2a-43f2-8005-3d300c631a75 attempt=1
27m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=f912241a-8e64-453e-bfaf-81d62e62b492 attempt=1 seq=1 type=agent.failed payload={"kind":"rate_limit","reason":"pi exited 1: 429 Rate limit exceeded: free-models-per-day. Add 5 credits to unlock 1000 free model requests per day"}
27m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=04e2444b-1b2a-43f2-8005-3d300c631a75 attempt=1 seq=0 type=agent.started payload={"runtime":"pi","cwd":"/tmp/task-04e2444b-1b2a-43f2-8005-3d300c631a75-a1-6jku75jb"}
26m ago
TaskRequeued
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=cc7bdce5-ee71-4c85-ba74-02be57773715 task_id=04e2444b-1b2a-43f2-8005-3d300c631a75 attempt=1 reason=pi exited 1: 429 Rate limit exceeded: free-models-per-day. Add 5 credits to unlock 1000 free model requests per day
26m ago
TaskStarted
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=cc7bdce5-ee71-4c85-ba74-02be57773715 task_id=f912241a-8e64-453e-bfaf-81d62e62b492 attempt=2
26m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=04e2444b-1b2a-43f2-8005-3d300c631a75 attempt=1 seq=1 type=agent.failed payload={"kind":"rate_limit","reason":"pi exited 1: 429 Rate limit exceeded: free-models-per-day. Add 5 credits to unlock 1000 free model requests per day"}
26m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=f912241a-8e64-453e-bfaf-81d62e62b492 attempt=2 seq=0 type=agent.started payload={"runtime":"pi","cwd":"/tmp/task-f912241a-8e64-453e-bfaf-81d62e62b492-a2-w5pxkjql"}
26m ago
TaskRequeued
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=cc7bdce5-ee71-4c85-ba74-02be57773715 task_id=f912241a-8e64-453e-bfaf-81d62e62b492 attempt=2 reason=pi exited 1: 429 Rate limit exceeded: free-models-per-day. Add 5 credits to unlock 1000 free model requests per day
26m ago
TaskStarted
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=cc7bdce5-ee71-4c85-ba74-02be57773715 task_id=04e2444b-1b2a-43f2-8005-3d300c631a75 attempt=2
26m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=f912241a-8e64-453e-bfaf-81d62e62b492 attempt=2 seq=1 type=agent.failed payload={"kind":"rate_limit","reason":"pi exited 1: 429 Rate limit exceeded: free-models-per-day. Add 5 credits to unlock 1000 free model requests per day"}
26m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=04e2444b-1b2a-43f2-8005-3d300c631a75 attempt=2 seq=0 type=agent.started payload={"runtime":"pi","cwd":"/tmp/task-04e2444b-1b2a-43f2-8005-3d300c631a75-a2-r427vsgp"}
26m ago
TaskRequeued
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=cc7bdce5-ee71-4c85-ba74-02be57773715 task_id=04e2444b-1b2a-43f2-8005-3d300c631a75 attempt=2 reason=pi exited 1: 429 Rate limit exceeded: free-models-per-day. Add 5 credits to unlock 1000 free model requests per day
26m ago
TaskStarted
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=cc7bdce5-ee71-4c85-ba74-02be57773715 task_id=f912241a-8e64-453e-bfaf-81d62e62b492 attempt=3
26m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=04e2444b-1b2a-43f2-8005-3d300c631a75 attempt=2 seq=1 type=agent.failed payload={"kind":"rate_limit","reason":"pi exited 1: 429 Rate limit exceeded: free-models-per-day. Add 5 credits to unlock 1000 free model requests per day"}
26m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=f912241a-8e64-453e-bfaf-81d62e62b492 attempt=3 seq=0 type=agent.started payload={"runtime":"pi","cwd":"/tmp/task-f912241a-8e64-453e-bfaf-81d62e62b492-a3-8s5p9f52"}
25m ago
TaskFailedEvent
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=cc7bdce5-ee71-4c85-ba74-02be57773715 task_id=f912241a-8e64-453e-bfaf-81d62e62b492 reason=pi exited 1: 429 Rate limit exceeded: free-models-per-day. Add 5 credits to unlock 1000 free model requests per day
25m ago
TaskStarted
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=cc7bdce5-ee71-4c85-ba74-02be57773715 task_id=04e2444b-1b2a-43f2-8005-3d300c631a75 attempt=3
25m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=f912241a-8e64-453e-bfaf-81d62e62b492 attempt=3 seq=1 type=agent.failed payload={"kind":"rate_limit","reason":"pi exited 1: 429 Rate limit exceeded: free-models-per-day. Add 5 credits to unlock 1000 free model requests per day"}
25m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=04e2444b-1b2a-43f2-8005-3d300c631a75 attempt=3 seq=0 type=agent.started payload={"runtime":"pi","cwd":"/tmp/task-04e2444b-1b2a-43f2-8005-3d300c631a75-a3-w3oygndd"}
25m ago
TaskFailedEvent
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=cc7bdce5-ee71-4c85-ba74-02be57773715 task_id=04e2444b-1b2a-43f2-8005-3d300c631a75 reason=pi exited 1: 429 Rate limit exceeded: free-models-per-day. Add 5 credits to unlock 1000 free model requests per day
25m ago
GoalFailedEvent
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b goal_id=cc7bdce5-ee71-4c85-ba74-02be57773715
25m ago
PlanFailed
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b reason=goal cc7bdce5-ee71-4c85-ba74-02be57773715 failed
25m ago
agent.event
plan_id=ed49f12a-ed41-4579-ab95-f968a115366b task_id=04e2444b-1b2a-43f2-8005-3d300c631a75 attempt=3 seq=1 type=agent.failed payload={"kind":"rate_limit","reason":"pi exited 1: 429 Rate limit exceeded: free-models-per-day. Add 5 credits to unlock 1000 free model requests per day"}