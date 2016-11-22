from compiler.js import get_package, split_name, escape
from compiler.js.code import process, parse_deps, generate_accessors, replace_enums
from compiler import lang
import json

class component_generator(object):
	def __init__(self, name, component, prototype = False):
		self.name = name
		self.component = component
		self.aliases = {}
		self.properties = {}
		self.enums = {}
		self.assignments = {}
		self.animations = {}
		self.package = get_package(name)
		self.base_type = None
		self.children = []
		self.methods = {}
		self.signal_handlers = {}
		self.changed_handlers = {}
		self.key_handlers = {}
		self.signals = set()
		self.elements = []
		self.id = None
		self.prototype = prototype
		self.ctor = ''

		for child in component.children:
			self.add_child(child)

	def collect_id(self, id_set):
		if self.id is not None:
			id_set.add(self.id)
		for g in self.assignments.itervalues():
			if type(g) is component_generator and g.id:
				g.collect_id(id_set)
		for g in self.animations.itervalues():
			if type(g) is component_generator and g.id:
				g.collect_id(id_set)
		for g in self.children:
			g.collect_id(id_set)

	def assign(self, target, value):
		t = type(value)
		if t is lang.Component:
			value = component_generator(self.package + ".<anonymous>", value)
		if t is str: #and value[0] == '"' and value[-1] == '"':
			value = value.replace("\\\n", "")
		self.assignments[target] = value

	def has_property(self, name):
		return (name in self.properties) or (name in self.aliases) or (name in self.enums)

	def add_child(self, child):
		t = type(child)
		if t is lang.Property:
			if self.has_property(child.name):
				raise Exception("duplicate property " + child.name)
			self.properties[child.name] = child
			if child.value is not None:
				if not child.is_trivial():
					self.assign(child.name, child.value)
		elif t is lang.AliasProperty:
			if self.has_property(child.name):
				raise Exception("duplicate property " + child.name)
			self.aliases[child.name] = child.target
		elif t is lang.EnumProperty:
			if self.has_property(child.name):
				raise Exception("duplicate property " + child.name)
			self.enums[child.name] = child
		elif t is lang.Assignment:
			if child.target == 'id':
				raise Exception('assigning non-id for id')
			self.assign(child.target, child.value)
		elif t is lang.IdAssignment:
			self.id = child.name
			self.assign("id", child.name)
		elif t is lang.Component:
			self.children.append(component_generator(self.package + ".<anonymous>", child))
		elif t is lang.Behavior:
			for target in child.target:
				if target in self.animations:
					raise Exception("duplicate animation on property " + target);
				self.animations[target] = component_generator(self.package + ".<anonymous-animation>", child.animation)
		elif t is lang.Method:
			name, args, code = child.name, child.args, child.code
			if child.event and len(name) > 2 and name != "onChanged" and name.startswith("on") and name[2].isupper(): #onXyzzy
				name = name[2].lower() + name[3:]
				if name.endswith("Pressed"):
					name = name[0].upper() + name[1:-7]
					if name in self.key_handlers:
						raise Exception("duplicate key handler " + child.name)
					self.key_handlers[name] = code
				elif name.endswith("Changed"):
					name = name[:-7]
					if name in self.changed_handlers:
						raise Exception("duplicate signal handler " + child.name)
					self.changed_handlers[name] = code
				else:
					if name in self.signal_handlers:
						raise Exception("duplicate signal handler " + child.name)
					self.signal_handlers[name] = args, code
			else:
				if name in self.methods:
					raise Exception("duplicate method " + name)
				self.methods[name] = args, code
		elif t is lang.Constructor:
			self.ctor = "\t//custom constructor:\n\t" + child.code + "\n"
		elif t is lang.Signal:
			name = child.name
			if name in self.signals:
				raise Exception("duplicate signal " + name)
			self.signals.add(name)
		elif t is lang.ListElement:
			self.elements.append(child.data)
		elif t is lang.AssignmentScope:
			for assign in child.values:
				self.assign(child.target + '.' + assign.target, assign.value)
		else:
			raise Exception("unhandled element: %s" %child)

	def call_create(self, registry, ident_n, target, value):
		assert isinstance(value, component_generator)
		ident = '\t' * ident_n
		code = '%s%s.__create()' %(ident, target)
		if not value.prototype:
			p, c = value.generate_creators(registry, target, ident_n)
			code += '\n' + p + '\n' + c
		return code

	def call_setup(self, registry, ident_n, target, value):
		assert isinstance(value, component_generator)
		ident = '\t' * ident_n
		code = '%s%s.__setup()' %(ident, target)
		if not value.prototype:
			code += '\n' + value.generate_setup_code(registry, target, ident_n)
		return code

	def generate_ctor(self, registry):
		return "\texports.%s.apply(this, arguments);\n" %(registry.find_component(self.package, self.component.name)) + self.ctor

	def get_base_type(self, registry):
		return registry.find_component(self.package, self.component.name)

	def generate(self, registry):
		base_type = self.get_base_type(registry)
		ctor  = "/**\n * @constructor\n"
		ctor += " * @extends {exports.%s}\n" %base_type
		ctor += " */\n"
		ctor += "\texports.%s = function(parent, _delegate) {\n%s\n}\n" %(self.name, self.generate_ctor(registry))
		return ctor

	def generate_animations(self, registry, parent):
		r = []
		for name, animation in self.animations.iteritems():
			var = "behavior_on_" + escape(name)
			r.append("\tvar %s = new _globals.%s(%s)" %(var, registry.find_component(self.package, animation.component.name), parent))
			r.append(self.call_create(registry, 1, var, animation))
			r.append(self.call_setup(registry, 1, var, animation))
			name_parent, target = split_name(name)
			if not parent:
				name_parent = 'this'
			else:
				name_parent = self.get_lvalue(parent, name_parent)
			r.append("\t%s.setAnimation('%s', %s);\n" %(name_parent, target, var))
		return "\n".join(r)

	def generate_prototype(self, registry, ident_n = 1):
		assert self.prototype == True

		#HACK HACK: make immutable
		registry.id_set = set(['context'])
		self.collect_id(registry.id_set)

		r = []
		ident = "\t" * ident_n

		r.append("%sexports.%s.prototype.componentName = '%s'" %(ident, self.name, self.name))

		for name in self.signals:
			r.append("%sexports.%s.prototype.%s = _globals.core.createSignal('%s')" %(ident, self.name, name, name))

		for name, argscode in self.methods.iteritems():
			args, code = argscode
			code = process(code, self, registry)
			r.append("%sexports.%s.prototype.%s = function(%s) %s" %(ident, self.name, name, ",".join(args), code))

		for name, prop in self.properties.iteritems():
			args = ["exports.%s.prototype" %self.name, "'%s'" %prop.type, "'%s'" %name]
			if prop.is_trivial():
				args.append(prop.value)
			r.append("%score.addProperty(%s)" %(ident, ", ".join(args)))

		for name, prop in self.enums.iteritems():
			values = prop.values

			for i in xrange(0, len(values)):
				r.append("/** @const @type {number} */")
				r.append("%sexports.%s.prototype.%s = %d" %(ident, self.name, values[i], i))
				r.append("/** @const @type {number} */")
				r.append("%sexports.%s.%s = %d" %(ident, self.name, values[i], i))

			args = ["exports.%s.prototype" %self.name, "'enum'", "'%s'" %name]
			if prop.default is not None:
				args.append("exports.%s.%s" %(self.name, prop.default))
			r.append("%score.addProperty(%s)" %(ident, ", ".join(args)))

		base_type = self.get_base_type(registry)
		p, code = self.generate_creators(registry, 'this', ident_n + 1)
		b = '\t%s_globals.%s.prototype.__create.apply(this)' %(ident, base_type)
		r.append('%sexports.%s.prototype.__create = function() {\n%s\n%s\n%s\n}' \
			%(ident, self.name, b, p, code))

		code = self.generate_setup_code(registry, 'this', ident_n + 1)
		b = '\t%s_globals.%s.prototype.__setup.apply(this)' %(ident, base_type)
		r.append('%sexports.%s.prototype.__setup = function() {\n%s\n%s\n}' \
			%(ident, self.name, b, code))

		r.append('')

		return "\n".join(r)

	def find_property(self, registry, property):
		if property in self.properties:
			return self.properties[property]
		if property in self.enums:
			return self.enums[property]
		if property in self.aliases:
			return self.aliases[property]

		base = registry.find_component(self.package, self.component.name)
		if base != 'core.CoreObject':
			return registry.components[base].find_property(registry, property)

	def check_target_property(self, registry, target):
		path = target.split('.')

		if len(path) > 1:
			if (path[0] in registry.id_set):
				return

			if not self.find_property(registry, path[0]):
				raise Exception('unknown property %s in %s (%s)' %(path[0], self.name, self.component.name))
		else: #len(path) == 1
			if not self.find_property(registry, target):
				raise Exception('unknown property %s in %s (%s)' %(target, self.name, self.component.name))

	def generate_creators(self, registry, parent, ident_n = 1):
		prologue = []
		r = []
		ident = "\t" * ident_n

		if not self.prototype:
			for name in self.signals:
				r.append("%s%s.%s = _globals.core.createSignal('%s').bind(%s)" %(ident, parent, name, name, parent))

			for name, prop in self.properties.iteritems():
				args = [parent, "'%s'" %prop.type, "'%s'" %name]
				if prop.is_trivial():
					args.append(prop.value)
				r.append("\tcore.addProperty(%s)" %(", ".join(args)))

			for name, prop in self.enums.iteritems():
				raise Exception('adding enums in runtime is unsupported, consider putting this property (%s) in prototype' %name)

		for idx, gen in enumerate(self.children):
			var = "%s_child%d" %(parent, idx)
			component = registry.find_component(self.package, gen.component.name)
			prologue.append("\tvar %s = new _globals.%s(%s)" %(var, component, parent))
			prologue.append("\t%s.addChild(%s)" %(parent, var));
			r.append(self.call_create(registry, ident_n, var, gen))

		for target, value in self.assignments.iteritems():
			if target == "id":
				if "." in value:
					raise Exception("expected identifier, not expression")
				r.append("%s%s._setId('%s')" %(ident, parent, value))
			elif target.endswith(".id"):
				raise Exception("setting id of the remote object is prohibited")
			else:
				self.check_target_property(registry, target)

			if isinstance(value, component_generator):
				var = "%s_%s" %(parent, escape(target))
				prologue.append('\tvar %s' %var)
				if target != "delegate":
					prologue.append("%s%s = new _globals.%s(%s)" %(ident, var, registry.find_component(value.package, value.component.name), parent))
					r.append(self.call_create(registry, ident_n, var, value))
					r.append('%s%s.%s = %s' %(ident, parent, target, var))
				else:
					code = "%svar %s = new _globals.%s(%s, true)\n" %(ident, var, registry.find_component(value.package, value.component.name), parent)
					code += self.call_create(registry, ident_n, var, value) + '\n'
					code += self.call_setup(registry, ident_n, var, value) + '\n'
					r.append("%s%s.%s = (function() {\n%s\n%s\nreturn %s\n}).bind(%s)" %(ident, parent, target, code, ident, var, parent))

		return "\n".join(prologue), "\n".join(r)

	def get_lvalue(self, parent, target):
		path = target.split(".")
		path = ["_get('%s')" %x for x in path]
		return "%s.%s" % (parent, ".".join(path))

	def get_target_lvalue(self, parent, target):
		path = target.split(".")
		path = ["_get('%s')" %x for x in path[:-1]] + [path[-1]]
		return "%s.%s" % (parent, ".".join(path))


	def generate_setup_code(self, registry, parent, ident_n = 1):
		r = []
		ident = "\t" * ident_n
		for name, target in self.aliases.iteritems():
			get, pname = generate_accessors(target)
			r.append("""\
	core.addAliasProperty(%s, '%s', (function() { return %s; }).bind(%s), '%s')
""" %(parent, name, get, parent, pname))
		for target, value in self.assignments.iteritems():
			if target == "id":
				continue
			t = type(value)
			#print self.name, target, value
			target_lvalue = self.get_target_lvalue(parent, target)
			if t is str:
				value = replace_enums(value, self, registry)
				deps = parse_deps(value)
				if deps:
					suffix = "_var_%s__%s" %(escape(parent), escape(target))
					var = "_update" + suffix
					r.append("%svar %s = (function() { %s = (%s); }).bind(%s)" %(ident, var, target_lvalue, value, parent))
					r.append("%s%s();" %(ident, var))
					undep = []
					for path, dep in deps:
						if dep == 'model':
							path, dep = "%s._get('_delegate')" %parent, '_row'
						r.append("%s%s.connectOnChanged(%s, '%s', %s);" %(ident, parent, path, dep, var))
						undep.append("%s.removeOnChanged('%s', _update%s)" %(path, dep, suffix))
					r.append("%s%s._removeUpdater('%s', (function() { %s }).bind(%s))" %(ident, parent, target, ";".join(undep), parent))
				else:
					r.append("%s%s._removeUpdater('%s'); %s = (%s);" %(ident, parent, target, target_lvalue, value))

			elif t is component_generator:
				if target == "delegate":
					continue
				var = "%s_%s" %(parent, escape(target))
				r.append('%svar %s = %s.%s' %(ident, var, parent, target))
				r.append(self.call_setup(registry, ident_n, var, value))
			else:
				raise Exception("skip assignment %s = %s" %(target, value))

		for idx, gen in enumerate(self.children):
			var = '%s_child%d' %(escape(parent), idx)
			r.append('%svar %s = %s.children[%d]' %(ident, var, parent, idx))
			r.append(self.call_setup(registry, ident_n, var, gen))

		if self.elements:
			r.append("\t%s.assign(%s)" %(parent, json.dumps(self.elements)))

		if not self.prototype:
			for name, argscode in self.methods.iteritems():
				args, code = argscode
				code = process(code, self, registry)
				r.append("%s%s.%s = (function(%s) %s ).bind(%s)" %(ident, parent, name, ",".join(args), code, parent))

		for name, argscode in self.signal_handlers.iteritems():
			args, code = argscode
			code = process(code, self, registry)
			if name != "completed":
				r.append("%s%s.on('%s', (function(%s) %s ).bind(%s))" %(ident, parent, name, ",".join(args), code, parent))
			else:
				r.append("%s%s._context._onCompleted((function() %s ).bind(%s))" %(ident, parent, code, parent))
		for name, code in self.changed_handlers.iteritems():
			code = process(code, self, registry)
			r.append("%s%s.onChanged('%s', (function(value) %s ).bind(%s))" %(ident, parent, name, code, parent))
		for name, code in self.key_handlers.iteritems():
			code = process(code, self, registry)
			r.append("%s%s.onPressed('%s', (function(key, event) %s ).bind(%s))" %(ident, parent, name, code, parent))
		r.append(self.generate_animations(registry, parent))
		return "\n".join(r)
