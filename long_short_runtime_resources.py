"""Linux read-only process/cgroup accounting; no blind memory-limit restarts."""
import os
from pathlib import Path


def snapshot(processes):
    rss={}
    pids={'supervisor':os.getpid()}
    pids.update({name:r['pid'] for name,r in processes.items() if r.get('state')=='RUNNING' and r.get('pid')})
    for name,pid in pids.items():
        try:
            fields=dict(line.split(':',1) for line in Path(f'/proc/{int(pid)}/status').read_text().splitlines() if ':' in line)
            rss[name]=round(int(fields.get('VmRSS','0 kB').split()[0])/1024,2)
        except (OSError,ValueError):continue
    out=dict(process_rss_mb=rss,total_process_rss_mb=round(sum(rss.values()),2))
    try:
        base=Path('/sys/fs/cgroup')
        out['cgroup_current_mb']=round(int((base/'memory.current').read_text())/1048576,2)
        fields=dict(line.split() for line in (base/'memory.stat').read_text().splitlines())
        out['cgroup_anon_mb']=round(int(fields.get('anon',0))/1048576,2)
        out['cgroup_file_cache_mb']=round(int(fields.get('file',0))/1048576,2)
        out['memory_events']={k:int(v) for k,v in (line.split() for line in (base/'memory.events').read_text().splitlines())}
    except (OSError,ValueError):pass
    return out
