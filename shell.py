import os
import shlex
import signal
import sys
from dataclasses import dataclass


@dataclass
class Job:
    pid: int
    command: str
    background: bool
    status: str = "Running"


jobs = {}
next_job_id = 1


def update_status(job, status):
    if os.WIFSTOPPED(status):
        job.status = "Stopped"
        job.background = True
    elif os.WIFCONTINUED(status):
        job.status = "Running"
    elif os.WIFSIGNALED(status):
        job.status = "Terminated"
    elif os.WIFEXITED(status):
        job.status = "Done"


def reap_jobs():

    while True:
        try:
            pid, status = os.waitpid(-1, os.WNOHANG | os.WUNTRACED | os.WCONTINUED)
        except ChildProcessError:
            return
        if pid == 0:
            return
        for job in jobs.values():
            if job.pid == pid:
                update_status(job, status)
                break


def wait_foreground(job_id):
    job = jobs[job_id]
    job.background = False

    interactive = sys.stdin.isatty()
    try:
        if interactive:
            os.tcsetpgrp(sys.stdin.fileno(), job.pid)
        if job.status == "Stopped":
            os.killpg(job.pid, signal.SIGCONT)
            job.status = "Running"
        _, status = os.waitpid(job.pid, os.WUNTRACED)
        update_status(job, status)
    finally:
        if interactive:
            os.tcsetpgrp(sys.stdin.fileno(), os.getpgrp())
    if job.status in ("Done", "Terminated"):
        del jobs[job_id]
    else:
        print(f"[{job_id}]  Stopped    {job.command}")


def get_job(args):
    if len(args) != 2:
        raise ValueError(f"usage: {args[0]} JOB_ID")
    job_id = int(args[1])
    if job_id not in jobs or jobs[job_id].status in ("Done", "Terminated"):
        raise ValueError(f"{args[0]}: no active job {job_id}")
    return job_id


def builtin(args):
    command = args[0]
    if command == "exit":
        if len(args) != 1:
            raise ValueError("usage: exit")
        return "exit"
    if command == "cd":
        if len(args) > 2:
            raise ValueError("usage: cd [directory]")
        os.chdir(os.path.expanduser(args[1] if len(args) == 2 else "~"))
    elif command == "jobs":
        if len(args) != 1:
            raise ValueError("usage: jobs")
        reap_jobs()
        for job_id, job in list(jobs.items()):
            print(f"[{job_id}]  {job.status:<10} {job.command}")

            if job.status in ("Done", "Terminated"):
                del jobs[job_id]
    elif command == "fg":
        job_id = get_job(args)
        print(jobs[job_id].command, flush=True)
        wait_foreground(job_id)
    elif command == "kill":
        job_id = get_job(args)
        job = jobs[job_id]
        os.killpg(job.pid, signal.SIGTERM)

        if job.status == "Stopped":
            os.killpg(job.pid, signal.SIGCONT)
        os.waitpid(job.pid, 0)
        print(f"[{job_id}]  Terminated  {job.command}")
        del jobs[job_id]
    else:
        return False
    return True


def launch(args, background):
    global next_job_id
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid == 0:
        try:
            os.setpgid(0, 0)
            for sig in (signal.SIGINT, signal.SIGQUIT, signal.SIGTSTP,
                        signal.SIGTTIN, signal.SIGTTOU):
                signal.signal(sig, signal.SIG_DFL)
            os.execvp(args[0], args)
        except OSError as error:
            message = "command not found" if isinstance(error, FileNotFoundError) else error.strerror
            os.write(2, f"myshell: {args[0]}: {message}\n".encode())
        os._exit(127)
    try:
        os.setpgid(pid, pid)
    except (PermissionError, ProcessLookupError):
        pass
    job_id = next_job_id
    next_job_id += 1
    jobs[job_id] = Job(pid, shlex.join(args), background)
    if background:
        print(f"[{job_id}] {pid} {jobs[job_id].command}")
    else:
        wait_foreground(job_id)


def cleanup():

    reap_jobs()
    for job in jobs.values():
        if job.status not in ("Done", "Terminated"):
            try:
                os.killpg(job.pid, signal.SIGKILL)
                os.waitpid(job.pid, 0)
            except (ProcessLookupError, ChildProcessError):
                pass


def main():
    if not hasattr(os, "fork"):
        sys.exit("Use Linux, macOS, or WSL: this shell requires os.fork().")

    for sig in (signal.SIGINT, signal.SIGQUIT, signal.SIGTSTP,
                signal.SIGTTIN, signal.SIGTTOU):
        signal.signal(sig, signal.SIG_IGN)
    try:
        while True:
            reap_jobs()
            try:
                line = input("myshell> ").strip()
            except EOFError:
                print()
                break
            if not line:
                continue
            try:

                lexer = shlex.shlex(line, posix=True, punctuation_chars="&|<>")
                lexer.whitespace_split = True
                lexer.commenters = ""
                tokens = list(lexer)
                background = bool(tokens and tokens[-1] == "&" and line.endswith("&"))
                if background:
                    line = line[:-1].rstrip()
                args = shlex.split(line)
                if not args:
                    raise ValueError("missing command before &")
                if builtin(args) == "exit":
                    break
                if args[0] not in ("cd", "jobs", "fg", "kill", "exit"):
                    launch(args, background)
            except (OSError, ValueError) as error:
                print(f"myshell: {error}", file=sys.stderr)
    finally:
        cleanup()


if __name__ == "__main__":
    main()
