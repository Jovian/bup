#!/usr/bin/env python2.5
import sys, re, errno, stat, client
import hashsplit, git, options
from helpers import *


saved_errors = []
def add_error(e):
    saved_errors.append(e)
    log('\n%s\n' % e)


def _direxpand(name):
    st = os.lstat(name)
    try:
        if stat.S_ISDIR(st.st_mode):
            for sub in os.listdir(name):
                subfull = os.path.join(name, sub)
                for fn_st in _direxpand(subfull):
                    yield fn_st
        else:
            yield (name,st)
    except OSError, e:
        if e.errno in [errno.ENOENT, errno.EPERM, errno.EACCES]:
            add_error(e)
        else:
            raise


def direxpand(names):
    for n in names:
        for fn_st in _direxpand(n):
            yield fn_st
            

def _normpath(dir):
    p = os.path.normpath(dir)
    return (p != '.') and p or ''


class Tree:
    def __init__(self, parent, name):
        assert(name != '.')
        assert(not (parent and not name))
        self.parent = parent
        self.name = name
        self.sha = None
        self.children = {}
        if self.parent:
            self.parent.children[self.name] = self
    
    def fullpath(self):
        if self.parent:
            return os.path.join(self.parent.fullpath(), self.name)
        else:
            return self.name
        
    def gettop(self):
        p = self
        while p.parent:
            p = p.parent
        return p
        
    def getdir(self, dir):
        # FIXME: deal with '..' somehow (look at how tar does it)
        dir = _normpath(dir)
        if dir.startswith('/'):
            dir = dir[1:]
        top = self.gettop()
        if not dir:
            return top
        for part in dir.split('/'):
            sub = top.children.get(part)
            if not sub:
                sub = top.children[part] = Tree(top, part)
            top = sub
        return top
    
    def addfile(self, mode, fullname, id):
        (dir, name) = os.path.split(fullname)
        self.getdir(dir).children[name] = (mode, name, id)
        
    def shalist(self, w):
        for c in self.children.values():
            if isinstance(c, tuple):  # sha1 entry for a file
                yield c
            else:  # tree
                t = ('40000', c.name, c.gen_tree(w))
                yield t
        
    def gen_tree(self, w):
        if not self.sha:
            self.sha = w.new_tree(self.shalist(w))
        return self.sha


optspec = """
bup save [-tc] [-n name] <filenames...>
--
r,remote=  remote repository path
t,tree     output a tree id
c,commit   output a commit id
n,name=    name of backup set to update (if any)
v,verbose  increase log output (can be used more than once)
"""
o = options.Options('bup save', optspec)
(opt, flags, extra) = o.parse(sys.argv[1:])

git.check_repo_or_die()
if not (opt.tree or opt.commit or opt.name):
    log("bup save: use one or more of -t, -c, -n\n")
    o.usage()

if opt.verbose >= 2:
    git.verbose = opt.verbose - 1
    hashsplit.split_verbosely = opt.verbose - 1

refname = opt.name and 'refs/heads/%s' % opt.name or None
if opt.remote:
    cli = client.Client(opt.remote)
    oldref = refname and cli.read_ref(refname) or None
    cli.sync_indexes()
    w = cli.new_packwriter()
else:
    cli = None
    oldref = refname and git.read_ref(refname) or None
    w = git.PackWriter()
    
root = Tree(None, '')
for (fn,st) in direxpand(extra):
    if opt.verbose:
        log('\n%s ' % fn)
    try:
        if stat.S_ISREG(st.st_mode):  # regular file
            f = open(fn)
            (mode, id) = hashsplit.split_to_blob_or_tree(w, [f])
        elif stat.S_ISLNK(st.st_mode):  # symlink
            (mode, id) = ('120000', w.new_blob(os.readlink(fn)))
        else:
            add_error(Exception('skipping special file "%s"' % fn))
    except IOError, e:
        add_error(e)
    except OSError, e:
        add_error(e)
    else:
        root.addfile(mode, fn, id)
tree = root.gen_tree(w)
if opt.verbose:
    log('\n')
if opt.tree:
    print tree.encode('hex')
if opt.commit or opt.name:
    msg = 'bup save\n\nGenerated by command:\n%r' % sys.argv
    ref = opt.name and ('refs/heads/%s' % opt.name) or None
    commit = w.new_commit(oldref, tree, msg)
    if opt.commit:
        if opt.verbose:
            log('\n')
        print commit.encode('hex')

w.close()  # must close before we can update the ref
        
if opt.name:
    if cli:
        cli.update_ref(refname, commit, oldref)
    else:
        git.update_ref(refname, commit, oldref)

if cli:
    cli.close()

if saved_errors:
    log('WARNING: %d errors encountered while saving.\n' % len(saved_errors))
