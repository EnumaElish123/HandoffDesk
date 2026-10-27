"""Calm, rounded desktop styling; native controls retain their normal behavior."""
import tkinter as tk
import sys
from tkinter import font as tkfont, ttk

from .rounded_assets import rounded_png, chevron_png

BG = '#F5F5F7'
WHITE = '#FFFFFF'
GREEN = ACCENT = '#0066CC'  # Compatibility name for existing callers.
INK = '#1D1D1F'
MUTED = '#63636A'
BORDER = '#DCDCE1'
SIDEBAR = '#EFEFF2'
ACCENT_SOFT = '#E5EFFB'
WARNING_BG = '#FFF5E6'
WARNING_INK = '#80521E'
FONT = 'Microsoft YaHei UI' if sys.platform == 'win32' else 'Noto Sans CJK SC'


def style_text(widget, editable=True):
    """Apply the same readable caret, selection and focus treatment everywhere."""
    widget.configure(insertbackground=ACCENT, selectbackground='#D6E8FE',
                     selectforeground=INK, inactiveselectbackground='#E6E7EB',
                     spacing1=2, spacing3=3, highlightthickness=1 if editable else 0,
                     highlightbackground=BORDER, highlightcolor=ACCENT, insertwidth=2)
    return widget


def _image_surfaces(root, style):
    """Install state images once per Tcl interpreter, keeping strong references."""
    owner = root._root()
    if hasattr(owner, '_resume_theme_images'):
        return
    images = owner._resume_theme_images = []

    def picture(fill, outline=None, radius=9, stroke=1, size=32):
        value = tk.PhotoImage(master=root, data=rounded_png(fill, outline, radius=radius,
                              stroke=stroke, size=size), format='png')
        images.append(value)
        return value

    def element(name, normal, states=(), *, radius=9, stroke=1, size=32, padding=2):
        image = picture(*normal, radius=radius, stroke=stroke, size=size)
        alternatives = []
        for specification, colors in states:
            alternatives.append((*specification, picture(*colors, radius=radius, stroke=stroke, size=size)))
        style.element_create(name, 'image', image, *alternatives,
                             border=radius + 2, padding=padding, sticky='nsew')

    chevron = tk.PhotoImage(master=root, data=chevron_png(MUTED), format='png')
    chevron_disabled = tk.PhotoImage(master=root, data=chevron_png('#B0B0B8'), format='png')
    images.extend((chevron, chevron_disabled))
    # Preserve native semantic suffixes used by ttk identify/hit testing.
    for name in ('Resume.Combobox.downarrow', 'Resume.Menubutton.indicator'):
        style.element_create(name, 'image', chevron, ('disabled', chevron_disabled),
                             border=0, sticky='')

    buttons = {
        'TButton': (WHITE, BORDER, '#F7F7F9', '#E8E8ED', '#EFEFF2'),
        'Primary.TButton': (ACCENT, ACCENT, '#0874DD', '#0053A9', '#DFE5ED'),
        'Ghost.TButton': (BG, BG, '#E9E9EE', '#DEDEE5', BG),
        'Card.Ghost.TButton': (WHITE, WHITE, '#F0F1F4', '#E4E7EC', WHITE),
        'Danger.TButton': (WHITE, WHITE, '#FFF0ED', '#FADFD9', WHITE),
        'Alert.TButton': (WARNING_BG, WARNING_BG, '#FFEDD0', '#F7DFB8', '#F4F1EC'),
        'Project.TButton': (SIDEBAR, SIDEBAR, '#E6E6EC', '#DCDCE6', SIDEBAR),
    }
    for index, (name, (normal, border, hover, pressed, disabled)) in enumerate(buttons.items()):
        states = [(('disabled',), (disabled, disabled)),
                  (('pressed', '!disabled'), (pressed, pressed))]
        if name == 'Project.TButton':
            states.extend([(('selected', 'focus'), (ACCENT_SOFT, ACCENT)),
                           (('selected',), (ACCENT_SOFT, ACCENT_SOFT))])
        states.extend([(('focus',), (normal, ACCENT if name != 'Primary.TButton' else '#A2C9F3')),
                       (('active',), (hover, hover if border == normal else '#C9CDD4'))])
        name_of_element = f'Resume.Button{index}.surface'
        element(name_of_element, (normal, border), states, stroke=1.5)
        style.layout(name, [(name_of_element, {'sticky':'nsew', 'children':[
            ('Button.focus', {'sticky':'nsew', 'children':[
                ('Button.padding', {'sticky':'nsew', 'children':[
                    ('Button.label', {'sticky':'nsew'})]})]})]})])

    element('Resume.Entry.surface', (WHITE, BORDER), [
        (('disabled',), ('#EFEFF2', '#E2E2E7')),
        (('focus',), (WHITE, ACCENT)), (('active',), (WHITE, '#B8BDC5'))], stroke=1.5)
    style.layout('TEntry', [('Resume.Entry.surface', {'sticky':'nsew', 'children':[
        ('Entry.padding', {'sticky':'nsew', 'children':[
            ('Entry.textarea', {'sticky':'nsew'})]})]})])
    # The native combobox still owns selection and hit testing; the downarrow
    # element keeps its semantic name while drawing only a fine chevron.
    style.layout('TCombobox', [('Resume.Entry.surface', {'sticky':'nsew', 'children':[
        ('Resume.Combobox.downarrow', {'side':'right', 'sticky':'ns'}),
        ('Combobox.padding', {'sticky':'nsew', 'children':[
            ('Combobox.textarea', {'sticky':'nsew'})]})]})])

    element('Resume.Menu.surface', (WHITE, WHITE), [
        (('disabled',), ('#F2F2F5', '#F2F2F5')),
        (('focus',), (WHITE, ACCENT)),
        (('active',), ('#EFEFF3', '#EFEFF3')), (('pressed',), ('#E5E5EB', '#E5E5EB'))])
    style.layout('TMenubutton', [('Resume.Menu.surface', {'sticky':'nsew', 'children':[
        ('Menubutton.focus', {'sticky':'nsew', 'children':[
            ('Resume.Menubutton.indicator', {'side':'right', 'sticky':''}),
            ('Menubutton.padding', {'sticky':'we', 'children':[
                ('Menubutton.label', {'side':'left', 'sticky':''})]})]})]})])

    element('Resume.Surface', (WHITE, '#E7E7EC'), radius=14, size=40, padding=1)
    style.layout('Surface.TFrame', [('Resume.Surface', {'sticky':'nsew'})])
    element('Resume.Tab', (BG, BG), [
        (('selected', 'focus'), (WHITE, ACCENT)), (('selected',), (WHITE, '#E3E3E9')),
        (('focus',), (BG, ACCENT)), (('active',), ('#E9E9EE', '#E9E9EE'))])
    style.layout('TNotebook.Tab', [('Resume.Tab', {'sticky':'nsew', 'children':[
        ('Notebook.padding', {'sticky':'nsew', 'children':[
            ('Notebook.focus', {'sticky':'nsew', 'children':[
                ('Notebook.label', {'sticky':'nsew'})]})]})]})])


def apply_theme(root):
    # Use available local fonts rather than downloading or substituting SF Pro.
    for name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkHeadingFont'):
        try:
            tkfont.nametofont(name, root=root).configure(family=FONT, size=10)
        except tk.TclError:
            pass
    for key, value in {'font':(FONT,10), 'background':WHITE, 'foreground':INK,
                       'selectBackground':ACCENT_SOFT, 'selectForeground':ACCENT}.items():
        root.option_add('*TCombobox*Listbox.' + key, value)
    for key, value in {'background':WHITE, 'foreground':INK,
                       'activeBackground':ACCENT_SOFT, 'activeForeground':ACCENT,
                       'relief':'flat', 'borderWidth':1, 'activeBorderWidth':0}.items():
        root.option_add('*Menu.' + key, value)

    style = ttk.Style(root)
    if 'resume_card_05' not in style.theme_names():
        style.theme_create('resume_card_05', parent='clam')
    style.theme_use('resume_card_05')
    style.configure('.', background=BG, foreground=INK, font=(FONT,10), bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER, focuscolor=ACCENT,
                    selectbackground=ACCENT_SOFT, selectforeground=ACCENT)
    for name, color in (('TFrame', BG), ('White.TFrame', WHITE), ('Card.TFrame', WHITE),
                        ('Surface.TFrame', BG), ('Sidebar.TFrame', SIDEBAR), ('Accent.TFrame', ACCENT_SOFT)):
        style.configure(name, background=color, borderwidth=0, relief='flat')
    style.configure('TLabel', background=BG, foreground=INK, font=(FONT,10))
    for name in ('White.TLabel', 'Card.TLabel'):
        style.configure(name, background=WHITE)
    for name in ('Heading.TLabel', 'Title.TLabel'):
        style.configure(name, font=(FONT,18,'bold'), foreground=INK)
    style.configure('Section.TLabel', font=(FONT,11,'bold'))
    for name in ('Muted.TLabel', 'Small.TLabel', 'Status.TLabel'):
        style.configure(name, foreground=MUTED, font=(FONT,9))
    for name in ('Card.Muted.TLabel', 'CardMuted.TLabel'):
        style.configure(name, background=WHITE, foreground=MUTED, font=(FONT,9))
    for name in ('Card.Section.TLabel', 'CardSection.TLabel'):
        style.configure(name, background=WHITE, foreground=INK, font=(FONT,11,'bold'))
    style.configure('Accent.TLabel', foreground=ACCENT, background=ACCENT_SOFT, font=(FONT,9))
    style.configure('TSeparator', background='#E6E6EB')

    style.configure('TButton', background=WHITE, foreground=INK, padding=(11,6),
                    anchor='center', borderwidth=0, relief='flat', focusthickness=0,
                    focuscolor=ACCENT, bordercolor=BORDER, lightcolor=WHITE, darkcolor=WHITE)
    # The widget background is the parent surface visible through transparent
    # corners. Colored state fills are rendered by the rounded image element.
    style.map('TButton', background=[],
              foreground=[('disabled','#929299')], bordercolor=[('focus',ACCENT)])
    style.configure('Primary.TButton', background=WHITE, foreground=WHITE, padding=(14,7),
                    bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT, focuscolor=WHITE)
    style.map('Primary.TButton', background=[],
              foreground=[('disabled','#727B87'),('!disabled',WHITE)],
              bordercolor=[('focus','#A2C9F3'),('!focus',ACCENT)])
    for name, background in (('Ghost.TButton',BG),('Card.Ghost.TButton',WHITE)):
        style.configure(name, background=background, foreground=MUTED, padding=(8,5),
                        lightcolor=background, darkcolor=background, bordercolor=background)
        style.map(name, background=[],
                  foreground=[('disabled','#929299'),('active',ACCENT),('!active',MUTED)],
                  bordercolor=[('focus',ACCENT)])
    style.configure('Danger.TButton', background=WHITE, foreground='#B13C30', padding=(8,5), bordercolor=WHITE)
    style.map('Danger.TButton', background=[],
              foreground=[('disabled','#929299'),('!disabled','#B13C30')],bordercolor=[('focus',ACCENT)])
    style.configure('Alert.TButton', background=WHITE, foreground=WARNING_INK, bordercolor=WARNING_BG)
    style.map('Alert.TButton', background=[],
              foreground=[('disabled','#929299'),('!disabled',WARNING_INK)],bordercolor=[('focus',ACCENT)])
    style.configure('Project.TButton', background=SIDEBAR, foreground=INK, anchor='w',
                    justify='left', padding=(11,10), bordercolor=SIDEBAR, font=(FONT,10))
    style.map('Project.TButton', background=[],
              foreground=[('disabled','#929299'),('selected',ACCENT),('!selected',INK)],
              bordercolor=[('focus',ACCENT),('selected',ACCENT_SOFT),('!selected',SIDEBAR)])
    style.configure('TMenubutton', background=WHITE, foreground=MUTED, borderwidth=0, padding=(8,6),
                    relief='flat', arrowsize=7, indicatorsize=7, indicatormargin=(4,0,2,0),
                    arrowcolor=MUTED, focusthickness=0, focuscolor=ACCENT)
    style.map('TMenubutton', background=[],foreground=[('active',ACCENT)],bordercolor=[('focus',ACCENT)])

    for name in ('TEntry','TCombobox'):
        style.configure(name, fieldbackground=WHITE, background=BG, foreground=INK,
                        bordercolor=BORDER, lightcolor=WHITE, darkcolor=WHITE, borderwidth=0,
                        padding=(7,5), insertcolor=ACCENT, arrowsize=12, arrowcolor=MUTED)
        style.map(name, fieldbackground=[('readonly',WHITE),('disabled','#EFEFF2')],
                  background=[],foreground=[('disabled','#929299'),('readonly',INK)],
                  bordercolor=[('focus',ACCENT),('active','#B8BDC5')],
                  selectbackground=[('readonly',WHITE)],selectforeground=[('readonly',INK)])
    style.configure('Sidebar.TEntry', background=SIDEBAR)
    style.configure('Card.TEntry', background=WHITE)
    style.configure('Card.TCombobox', background=WHITE)
    style.configure('TCheckbutton', background=BG, foreground=INK, padding=(0,3),
                    indicatorbackground=WHITE, indicatorforeground=ACCENT)
    style.map('TCheckbutton', background=[('active',BG)], indicatorbackground=[('selected',ACCENT)],
              indicatorforeground=[('selected',WHITE)], foreground=[('disabled','#929299')])
    style.configure('Card.TCheckbutton', background=WHITE)
    style.map('Card.TCheckbutton', background=[('active',WHITE),('!active',WHITE)])

    style.configure('TNotebook', background=BG, borderwidth=0, tabmargins=(0,8,0,12),
                    lightcolor=BG, darkcolor=BG, bordercolor=BG)
    style.configure('TNotebook.Tab', background=BG, foreground=MUTED, padding=(13,6),
                    borderwidth=0, focuscolor=ACCENT, focusthickness=0, font=(FONT,10))
    style.map('TNotebook.Tab', background=[],
              foreground=[('selected',INK),('active',INK)])
    style.configure('Treeview', background=WHITE, fieldbackground=WHITE, foreground=INK,
                    rowheight=34, borderwidth=0, font=(FONT,10), relief='flat')
    style.layout('Treeview', [('Treeview.treearea', {'sticky':'nsew'})])
    style.map('Treeview', background=[('selected',ACCENT_SOFT)],foreground=[('selected',ACCENT)])
    style.configure('Treeview.Heading', background='#F5F5F8',foreground=MUTED,
                    padding=(10,8),borderwidth=0,relief='flat',font=(FONT,9))
    style.layout('Treeview.Heading', [('Treeheading.cell',{'sticky':'nsew'}),
        ('Treeheading.padding',{'sticky':'nsew','children':[('Treeheading.text',{'sticky':'w'})]})])
    for name in ('Vertical.TScrollbar','Horizontal.TScrollbar'):
        style.configure(name,gripcount=0,background='#C6C6CE',troughcolor=WHITE,
                        bordercolor=WHITE,lightcolor='#C6C6CE',darkcolor='#C6C6CE',
                        arrowsize=10,borderwidth=0,relief='flat')
        style.map(name,background=[('active','#A5A5B0'),('pressed','#858590')])
    style.configure('Horizontal.TProgressbar', background=ACCENT, troughcolor='#E5E9EF',
                    bordercolor='#E5E9EF', lightcolor=ACCENT, darkcolor=ACCENT,
                    borderwidth=0, thickness=4, troughrelief='flat')
    _image_surfaces(root, style)
    return style
