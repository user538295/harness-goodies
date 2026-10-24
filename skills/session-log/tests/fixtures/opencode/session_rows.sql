-- Rows in the shapes an OpenCode v2.0.30 store holds (keys and value types read from a real store; every text, path and id is a
-- placeholder). Loaded into the tables of tests/opencode_db.py at test time. Times are epoch milliseconds: 1791880920000 is 2026-10-13 08:42:00 UTC.
INSERT INTO session_v2 (id, project_id, parent_id, slug, directory, title, version, agent, time_created, time_updated) VALUES
  ('ses_fixture_root', 'prj_fixture', NULL, 'quiet-fixture', '/work/fixture', 'Fixture session', '2.0.30', 'build', 1791880930000, 1791881010000),
  ('ses_fixture_child', 'prj_fixture', 'ses_fixture_root', 'helper-fixture', '/work/fixture', 'Fixture helper', '2.0.30', 'general', 1791880950000, 1791880990000);

INSERT INTO session_message (id, session_id, type, seq, time_created, time_updated, data) VALUES
  ('msg_01', 'ses_fixture_root', 'user', 1, 1791880930000, 1791880930000,
   '{"text":"placeholder question","time":{"created":1791880930000},"files":[],"agents":[]}'),
  ('msg_02', 'ses_fixture_root', 'assistant', 2, 1791880931000, 1791880940000,
   '{"agent":"build","model":{"providerID":"fixture-provider","id":"fixture-model","variant":"default"},"content":[{"type":"reasoning","text":"placeholder thought","time":{"created":1791880931000,"completed":1791880932000},"state":"completed"},{"type":"tool","id":"call_01","name":"bash","state":{"status":"completed"},"time":{"created":1791880933000,"completed":1791880934000},"executed":true}],"finish":"tool-calls","rawFinish":"tool_use","cost":0.01,"tokens":{"input":1000,"output":50,"reasoning":10,"cache":{"read":200,"write":300}},"time":{"created":1791880931000,"completed":1791880935000},"snapshot":{"start":"s1","end":"s2"}}'),
  ('msg_03', 'ses_fixture_root', 'synthetic', 3, 1791880936000, 1791880936000,
   '{"text":"The following tool was executed by the user","time":{"created":1791880936000},"metadata":{},"description":"placeholder"}'),
  ('msg_04', 'ses_fixture_root', 'assistant', 4, 1791880937000, 1791880950000,
   '{"agent":"build","model":{"providerID":"fixture-provider","id":"fixture-model","variant":"default"},"content":[{"type":"reasoning","text":"more thought","time":{"created":1791880937000},"state":"completed"},{"type":"text","text":"placeholder answer part one"},{"type":"tool","id":"call_02","name":"read","state":{"status":"completed"},"time":{"created":1791880938000},"executed":true},{"type":"text","text":"placeholder answer part two"}],"finish":"stop","rawFinish":"end_turn","cost":0.02,"tokens":{"input":2000,"output":80,"reasoning":0,"cache":{"read":1200,"write":0}},"time":{"created":1791880937000,"completed":1791880945000},"snapshot":{"start":"s2","end":"s3"}}'),
  ('msg_05', 'ses_fixture_root', 'idle', 5, 1791880950000, 1791880950000, '{"time":{"created":1791880950000},"outcome":"completed"}'),
  ('msg_06', 'ses_fixture_root', 'user', 6, 1791880960000, 1791880960000,
   '{"text":"second placeholder question","time":{"created":1791880960000},"files":[{"uri":"file:///work/fixture/a.txt"}],"agents":[{"name":"general"}]}'),
  ('msg_07', 'ses_fixture_root', 'model-switched', 7, 1791880961000, 1791880961000,
   '{"time":{"created":1791880961000},"model":{"providerID":"fixture-provider","id":"other-model","variant":"default"},"previous":{"providerID":"fixture-provider","id":"fixture-model","variant":"default"}}'),
  ('msg_08', 'ses_fixture_root', 'assistant', 8, 1791880962000, 1791880970000,
   '{"agent":"build","model":{"providerID":"fixture-provider","id":"other-model","variant":"default"},"content":[],"finish":"error","error":{"name":"placeholder","message":"placeholder error"},"cost":0,"tokens":{"input":0,"output":0,"reasoning":0,"cache":{"read":0,"write":0}},"time":{"created":1791880962000,"completed":1791880963000},"retry":{"attempt":1}}'),
  ('msg_09', 'ses_fixture_root', 'assistant', 9, 1791880964000, 1791881000000,
   '{"agent":"build","model":{"providerID":"fixture-provider","id":"other-model","variant":"default"},"content":[{"type":"text","text":"second placeholder answer"}],"finish":"stop","rawFinish":"text","cost":0,"tokens":{"input":300,"output":40,"reasoning":5,"cache":{"read":0,"write":0}},"time":{"created":1791880964000,"completed":1791881000000}}'),
  ('msg_10', 'ses_fixture_root', 'shell', 10, 1791881001000, 1791881001000,
   '{"metadata":{},"time":{"created":1791881001000},"shellID":"sh_01","command":"ls","status":"completed","exit":0,"output":"placeholder"}'),
  ('msg_11', 'ses_fixture_root', 'compaction', 11, 1791881005000, 1791881010000,
   '{"status":"completed","reason":"auto","summary":"placeholder","recent":[],"time":{"created":1791881005000},"model":{"providerID":"fixture-provider","id":"fixture-model","variant":"default"},"cost":0.03,"tokens":{"input":9000,"output":500,"reasoning":0,"cache":{"read":0,"write":0}}}'),
  ('msg_c1', 'ses_fixture_child', 'user', 1, 1791880950000, 1791880950000, '{"text":"placeholder task for the helper","time":{"created":1791880950000},"files":[],"agents":[]}'),
  ('msg_c2', 'ses_fixture_child', 'assistant', 2, 1791880951000, 1791880990000,
   '{"agent":"general","model":{"providerID":"fixture-provider","id":"fixture-model","variant":"default"},"content":[{"type":"text","text":"placeholder helper result"}],"finish":"stop","rawFinish":"end_turn","cost":0.005,"tokens":{"input":400,"output":60,"reasoning":0,"cache":{"read":50,"write":20}},"time":{"created":1791880951000,"completed":1791880990000}}');
