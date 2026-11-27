" Basaltwater terminal defaults. Personal overrides belong in ~/.vimrc.
" Load Vim's runtime defaults before the site and personal overrides. Otherwise
" Vim loads them last when an account has no vimrc of its own.
if exists('g:skip_defaults_vim')
  filetype plugin indent on
  syntax enable
else
  source $VIMRUNTIME/defaults.vim
  let g:skip_defaults_vim = 1
endif

" Show line numbers and keep the status line visible in a single window.
set number laststatus=2 ruler
set autoindent

" Wrap at word boundaries and align continuation lines with the original indent.
set linebreak breakindent
" :set list reveals tabs, trailing spaces and nonbreaking spaces on demand.
set listchars=tab:>-,trail:~,nbsp:+,extends:>,precedes:<

" Keep edits when switching buffers and open new splits below or to the right.
set hidden splitbelow splitright

" Complete file and command names with Tab.
set wildmenu wildmode=longest:full,full

" Lowercase searches ignore case. An uppercase letter requests an exact case match.
set incsearch hlsearch ignorecase smartcase
if empty(mapcheck('<C-L>', 'n'))
  nnoremap <silent> <C-L> :nohlsearch<CR><C-L>
endif

" Keep quickfix output parseable even when the shell's rg config requests JSON.
if executable('rg')
  set grepprg=rg\ --no-config\ --vimgrep\ --smart-case\ --color=never
  set grepformat=%f:%l:%c:%m
endif
