# Gogs Git service

`--gogs` installs a minimal self-hosted Gogs instance with Git-over-SSH, Git
LFS over HTTP/HTTPS, SQLite storage, nginx integration when a hostname is
supplied, and a managed weekly release update. The setup creates a locked
`git` account. Gogs restricts each enrolled SSH key to its repository dispatcher.

## Choose a topic

<a id="choose-a-deployment-mode"></a>
<a id="first-login-and-git-access"></a>

- [Install and access Gogs](gogs-install.md) Choose a deployment mode, sign in, and set up Git access.

<a id="data-service-and-updates"></a>
<a id="rehearse-recovery"></a>
<a id="troubleshooting"></a>

- [Maintain and recover Gogs](gogs-maintenance.md) Manage data and updates, rehearse recovery, and troubleshoot.

<a id="github-primary-with-a-local-gogs-mirror-and-lfs"></a>
<a id="dedicated-mixed-media-vm"></a>

- [Gogs mirrors and dedicated VMs](gogs-mirrors.md) Set up a local GitHub mirror with LFS or a dedicated mixed-media VM.
