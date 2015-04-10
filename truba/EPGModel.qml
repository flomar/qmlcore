ListModel {
	property string	channel;
	property Object epgMap;
	property bool	isBusy: false;

	getEPGForChannel(channel): {
		this.channel = channel;
		this.clear();
		for (var i in this.epgMap[channel]) {
			var start = this.epgMap[channel][i].start;
			start = start.getHours() + ":" + (start.getMinutes() < 10 ? "0" : "") + start.getMinutes();
			this.append({
				title: this.epgMap[channel][i].title,
				start: start
			});
		}
	}

	onIsBusyChanged: {
		if (!this.isBusy && this.channel)
			this.getEPGForChannel();
	}

	//TODO: add epg update after 24 hours.

	update: {
		if (!this.protocol)
			return;

		this.isBusy = true;
		this.epgMap = {};
		var self = this;
		this.protocol.getProgramsAtDate(new Date(), function(programs) {
			for (var i in programs) {
				var channel = programs[i].channel;
				if (!self.epgMap[channel])
					self.epgMap[channel] = [];
				self.epgMap[channel].push({
					title: programs[i].title,
					start: new Date(programs[i].start),
					stop: new Date(programs[i].stop)
				});
			}
			self.isBusy = false;
		})
	}

	onCompleted:		{ this.update(); }
	onProtocolChanged:	{ this.update(); }
}
